import labscript_utils.h5_lock
import zmq
import h5py
import json
import numpy as np
from blacs.tab_base_classes import Worker
from labscript_utils import properties
import logging
import time

# JSON Encoder for Numpy types
class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(NumpyEncoder, self).default(obj)

# 配置 ARTIQ 接收端的 IP 和端口
# 实际使用中建议通过 connection_table 的 properties 传入
# ARTIQ_HOST = "127.0.0.1"  # 若 ARTIQ 在本机运行则使用 localhost
# PUB_PORT = 5555               # 发送脚本的端口
# SUB_PORT = 5556               # 接收反馈的端口 (可选)

class ARTIQ_Worker(Worker):
    def init(self):
        """初始化 ZMQ 连接"""
        self.context = zmq.Context()
        
        # 获取初始化参数
        self.device_map = getattr(self, 'initialisation_args', {}).get('device_map', {})
        self.current_state = {} # 缓存当前所有设备的状态
        
        # 获取网络配置
        init_args = getattr(self, 'initialisation_args', {})
        self.artiq_ip = init_args.get('artiq_ip', '127.0.0.1')
        self.broadcast_port = init_args.get('broadcast_port', 5555)
        self.feedback_port = init_args.get('feedback_port', 5556)

        # 1. 建立发布 Socket (用于发送脚本)
        # 使用 connect 模式，假设 ARTIQ 端 bind 了端口等待连接
        # 或者 bind，ARTIQ connect；这里采用 Worker bind (广播源) 的方式更符合 Broadcast 语义
        self.pub_socket = self.context.socket(zmq.PUB)
        self.pub_socket.bind(f"tcp://*:{self.broadcast_port}")
        
        # 2. 建立接收 Socket (用于等待 ARTIQ 执行完成的反馈)
        # 这是一个阻塞式的 PULL socket
        self.pull_socket = self.context.socket(zmq.PULL)
        self.pull_socket.bind(f"tcp://*:{self.feedback_port}")
        
        self.is_running = False # 标记实验是否正在运行
        self.experiment_finished = False # 实验完成标志
        self.execution_status = None
        self.execution_error_message = None
        self.shot_start_time = None
        self.minimum_shot_duration = 0.0

        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)
        print(f"[ARTIQ Worker] Broadcasting on port {self.broadcast_port}, listening for feedback on {self.feedback_port}")

    def transition_to_buffered(self, device_name, h5file, initial_values, fresh):
        """
        实验运行阶段：读取 HDF5 中的 ARTIQ 脚本并广播
        """
        # 0. 清空接收缓冲区
        # 防止之前的 Manual 模式或上一次实验残留的消息干扰
        while self.pull_socket.poll(0):
            try:
                self.pull_socket.recv()
            except:
                pass

        self.is_running = False
        self.experiment_finished = False
        self.execution_status = None
        self.execution_error_message = None
        self.shot_start_time = None
        self.minimum_shot_duration = self._get_minimum_shot_duration(h5file, device_name)

        # --- 握手检查 (Handshake) ---
        # 确保 Subscriber 处于在线且空闲状态
        print("[ARTIQ Worker] Pinging subscriber to check availability...")
        self.pub_socket.send_multipart([
            b'ARTIQ_EXECUTE', 
            json.dumps({'type': 'PING'}, cls=NumpyEncoder).encode('utf-8')
        ])
        
        try:
            # 等待 PING 响应 (2秒超时)
            if self.pull_socket.poll(timeout=2000):
                status = self.pull_socket.recv_json()
                if status.get('status') != 'IDLE':
                     raise RuntimeError(f"Subscriber is not IDLE. Status: {status.get('status')}")
                print("[ARTIQ Worker] Subscriber is IDLE and ready.")
            else:
                raise RuntimeError("Timeout waiting for subscriber PING response. Is the subscriber running?")
        except Exception as e:
            print(f"[Error] Handshake failed: {e}")
            raise e

        # 1. 从 HDF5 读取生成的 Python 脚本
        script_content = ""
        with h5py.File(h5file, 'r') as f:
            # 注意：路径需与 labscript_device.py 中 generate_code 写入的路径一致
            group = f['devices'][device_name]
            if 'ARTIQ_SCRIPT' in group:
                data = group['ARTIQ_SCRIPT'][0]
                # 处理编码问题 (HDF5 存储字符串可能为 bytes)
                if isinstance(data, bytes):
                    script_content = data.decode('utf-8')
                else:
                    script_content = str(data)
            else:
                print(f"[Error] No ARTIQ_SCRIPT found in {h5file}")
                return {}

        # 2. 封装消息
        message = {
            'type': 'BUFFERED',
            'filename': h5file,
            'script': script_content
        }

        # 3. 广播发送
        print(f"[ARTIQ Worker] Broadcasting experiment script ({len(script_content)} bytes)...")
        # 使用 multipart 发送：[Topic, JSON_Data]
        self.pub_socket.send_multipart([
            b'ARTIQ_EXECUTE', 
            json.dumps(message, cls=NumpyEncoder).encode('utf-8')
        ])

        # 4. 等待接收确认 (ACK)
        print("[ARTIQ Worker] Waiting for reception acknowledgement...")
        try:
            # 设置短超时 (例如 10 秒) 等待 ACK
            if self.pull_socket.poll(timeout=10000):
                ack = self.pull_socket.recv_json()
                if ack.get('status') == 'received':
                    print("[ARTIQ Worker] Script received by subscriber.")
                else:
                    print(f"[Warning] Unexpected message during ACK phase: {ack}")
            else:
                raise RuntimeError("Timeout waiting for ARTIQ subscriber acknowledgement. Is the subscriber running?")
        except Exception as e:
            print(f"[Error] ACK failed: {e}")
            # 如果没有接收到确认，抛出异常终止实验
            raise e

        # 5. 标记实验开始运行
        # 不在这里同步等待完成，而是交给 check_if_done 轮询
        self.is_running = True
        self.shot_start_time = time.time()
        print("[ARTIQ Worker] Experiment started. Monitoring status in background...")

        # 返回最终状态字典 (BLACS 要求)
        return {}

    def start_run(self):
        """
        启动实验。
        对于 Master Pseudoclock，这个方法被调用后，BLACS 会等待我们通知它实验完成。
        """
        print("[ARTIQ Worker] Starting run.")
        return {}

    def abort_buffered(self):
        """
        当实验在运行过程中被终止时调用。
        """
        print("[ARTIQ Worker] Aborting buffered run.")
        self.is_running = False
        self.experiment_finished = True
        self.execution_status = 'aborted'
        return True

    def transition_to_manual(self):
        """
        实验结束后，从 Buffered 模式恢复到 Manual 模式时调用。
        这个方法可以被后台线程直接调用以通知BLACS实验结束。
        """
        print("[ARTIQ Worker] Transitioning to manual mode (called from monitor thread).")
        self.is_running = False

        # 调用父类的transition_to_manual（如果存在）
        # 同时返回True表示成功，这是BLACS所期望的
        if hasattr(super(), 'transition_to_manual'):
            return super().transition_to_manual()
        return True

    def abort_transition_to_manual(self):
        """
        恢复到 Manual 模式失败时调用。
        """
        print("[ARTIQ Worker] Aborting transition to manual.")
        return True

    def abort_transition_to_buffered(self):
        """
        当 transition_to_buffered 失败时被调用。
        用于清理资源或重置状态。
        """
        print("[ARTIQ Worker] Aborting transition to buffered.")
        return True

    def program_manual(self, values):
        """
        手动模式：GUI 参数改变时，生成 ARTIQ 脚本并发送
        目前禁用实际发送功能，仅更新内部状态，防止干扰实验流程。
        """
        # 1. 更新当前状态缓存
        # values 可能是嵌套字典 (DDS)，需要深度更新
        for name, val in values.items():
            if name in self.current_state and isinstance(self.current_state[name], dict) and isinstance(val, dict):
                self.current_state[name].update(val)
            else:
                self.current_state[name] = val
        
        # 2. 不发送任何指令给 ARTIQ
        # print("[ARTIQ Worker] Manual update received but ignored (Manual mode disabled).")
        
        return values

    def check_if_done(self):
        """
        检查实验状态。
        """
        self.logger.debug(f"[check_status] called. is_running={self.is_running}, experiment_finished={self.experiment_finished}")
        
        # 如果实验已标记完成，则返回True通知BLACS结束
        if self.experiment_finished:
            self.logger.debug("[check_status] experiment_finished is True, returning True")
            return True
            
        # 原有的消息处理逻辑（用于处理过程中的打印消息等）
        try:
            while self.pull_socket.poll(10):
                result = self.pull_socket.recv_json()
                status = result.get('status')
                self.logger.debug(f"[check_status] Received message: {result}")
                
                if status == 'error':
                    self.execution_error_message = result.get('message')
                    print(f"[ARTIQ Error] {self.execution_error_message}")
                    self.is_running = False
                    self.execution_status = 'error'
                    self.experiment_finished = True
                    return True
                elif status == 'ok':
                    print("[ARTIQ Worker] ARTIQ finished successfully.")
                    self.is_running = False
                    self.execution_status = 'ok'
                    if self._minimum_duration_elapsed():
                        self.experiment_finished = True
                        return True
                elif status == 'print':
                    msg = result.get('message', '')
                    print(f"[ARTIQ Print] {msg}")
        except Exception as e:
            print(f"[Error] in check_status: {e}")
            self.is_running = False
            self.experiment_finished = True
            self.execution_status = 'error'
            self.execution_error_message = str(e)
            return True

        if self.execution_status == 'ok' and self._minimum_duration_elapsed():
            self.experiment_finished = True
            return True

        return False

    def _minimum_duration_elapsed(self):
        if self.shot_start_time is None:
            return False
        elapsed = time.time() - self.shot_start_time
        return elapsed >= self.minimum_shot_duration

    def _get_minimum_shot_duration(self, h5file, artiq_device_name):
        child_max_end_time = 0.0
        master_end_time = 0.0
        with h5py.File(h5file, 'r') as hdf5_file:
            device_group = hdf5_file.get('devices', {})
            if not device_group:
                return 0.0

            for device_name in device_group.keys():
                group = device_group[device_name]
                try:
                    device_props = properties.get(hdf5_file, device_name, 'device_properties')
                except Exception:
                    device_props = {}

                stop_time = float(device_props.get('stop_time', 0.0))
                t_start = float(group.attrs.get('t_start', 0.0))
                end_time = t_start + stop_time

                if device_name == artiq_device_name:
                    master_end_time = end_time
                else:
                    child_max_end_time = max(child_max_end_time, end_time)

        minimum_duration = max(child_max_end_time, master_end_time)
        print(
            "[ARTIQ Worker] Minimum master holdoff set to "
            f"{minimum_duration:.6f} s "
            f"(children={child_max_end_time:.6f} s, master={master_end_time:.6f} s)"
        )
        return minimum_duration

    def shutdown(self):
        """清理资源"""   
        self.pub_socket.close()
        self.pull_socket.close()
        self.context.term()
