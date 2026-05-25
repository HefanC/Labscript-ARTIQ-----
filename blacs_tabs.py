from blacs.device_base_class import DeviceTab, define_state
from blacs.tab_base_classes import MODE_MANUAL, MODE_BUFFERED, MODE_TRANSITION_TO_BUFFERED, MODE_TRANSITION_TO_MANUAL
from qtutils.qt.QtWidgets import QLayout, QLabel, QVBoxLayout, QWidget
import copy
import re
import time

class ARTIQ_MasterTab(DeviceTab):
    def initialise_workers(self):
        # 1. 构建设备映射表 (Labscript Name -> ARTIQ Connection & Type)
        # 以便 Worker 在生成手动控制脚本时知道如何引用设备
        connection_object = self.settings['connection_table'].find_by_name(self.device_name)
        device_map = {}
        
        self.experiment_complete = False

        def walk_device_map(device):
            # 提取连接信息
            # device.properties 包含 connection_table_properties
            # device.name 是 Labscript 变量名
            # device.parent_port 是连接端口 (如 'ttl0')
            
            conn = device.parent_port
            if not conn:
                conn = device.name # Fallback
                
            if 'DigitalOut' in device.device_class:
                device_map[device.name] = {'type': 'TTL', 'conn': conn}
            elif 'AnalogOut' in device.device_class:
                # Fastino 通道，conn 可能是 'channel0'
                # 需要解析出索引
                match = re.search(r'(\d+)$', conn)
                idx = int(match.group(1)) if match else 0
                device_map[device.name] = {'type': 'DAC', 'conn': 'fastino0', 'channel': idx}
            elif 'DDS' in device.device_class:
                # Urukul 通道
                device_map[device.name] = {'type': 'DDS', 'conn': conn}
                
            for child in device.child_list.values():
                walk_device_map(child)
                
        walk_device_map(connection_object)

        # 2. 获取连接表属性
        props = connection_object.properties
        
        worker_initialisation_args = {
            'artiq_ip': props.get('artiq_ip', '127.0.0.1'),
            'broadcast_port': props.get('broadcast_port', 5555),
            'feedback_port': props.get('feedback_port', 5556),
            'device_map': device_map
        }
        
        # 3. 启动 Worker
        # 使用 user_devices.ARTIQ... 路径，与 register_classes.py 保持一致
        self.create_worker(
            'main_worker',
            'user_devices.ARTIQ.blacs_worker.ARTIQ_Worker',
            worker_initialisation_args
        )
        self.primary_worker = "main_worker"

    @define_state(MODE_BUFFERED, True)
    def start_run(self, notify_queue):
        self.wait_until_done(notify_queue)
    
    @define_state(MODE_BUFFERED, True)
    def wait_until_done(self, notify_queue):
        """Call check_if_done repeatedly in the worker until the shot is complete"""
        done = yield (self.queue_work(self.primary_worker, 'check_if_done'))
        # Experiment is over. Tell the queue manager about it:
        if done:
            notify_queue.put('done')
        else:
            # Not actual recursion since this just queues up another call
            # after we return:
            self.wait_until_done(notify_queue)


    def initialise_gui(self):
        # 1. 输出一段文字信息 (GUI 顶部状态栏)
        layout = self.get_tab_layout()
        
        # 创建标题和状态标签
        title = QLabel("<h3>ARTIQ Kasli-SoC Control Interface</h3>")
        self.status_label = QLabel("Status: <b>Online</b> - Ready for commands")
        
        layout.addWidget(title)
        layout.addWidget(self.status_label)
        
        # 获取连接表对象
        connection_object = self.settings['connection_table'].find_by_name(self.device_name)
        
        # 寻找所有子设备 (TTL, DAC, DDS)
        ttls = {}
        dacs = {}
        ddss = {}
        
        def walk_device(device):
            # 检查设备类型
            device_class = device.device_class
            
            # 2. 特定TTL端口开关 (DigitalOut -> Checkbox)
            if 'DigitalOut' in device_class:
                ttls[device.name] = {
                    'label': device.name, # 显示名称
                    'invert': False,      # 是否反转逻辑
                    'worker': 'main_worker' # 显式指定 worker，防止 KeyError: None
                }
                
            # 3. 特定DAC端口输出特定电压 (AnalogOut -> SpinBox)
            elif 'AnalogOut' in device_class:
                dacs[device.name] = {
                    'base_unit': 'V',
                    'min': -10.0,
                    'max': 10.0,
                    'step': 0.01,
                    'decimals': 3,
                    'label': device.name,
                    'worker': 'main_worker' # 显式指定 worker
                }
                
            # 4. 特定DDS端口输出特定频率的信号 (DDS -> Freq/Amp/Phase SpinBoxes)
            elif 'DDS' in device_class: 
                ddss[device.name] = {
                    'freq': {
                        'base_unit': 'Hz',
                        'min': 0.0,
                        'max': 400e6, # 400 MHz Max
                        'step': 1e6,  # 1 MHz step
                        'decimals': 6,
                        'worker': 'main_worker' # 显式指定 worker
                    },
                    'amp': {
                        'base_unit': 'V', # 幅度 (0-1 或 V)
                        'min': 0.0,
                        'max': 1.0,
                        'step': 0.01,
                        'decimals': 3,
                        'worker': 'main_worker' # 显式指定 worker
                    },
                    'phase': {
                        'base_unit': 'deg',
                        'min': 0.0,
                        'max': 360.0,
                        'step': 1.0,
                        'decimals': 2,
                        'worker': 'main_worker' # 显式指定 worker
                    }
                }
            
            # 递归查找子设备
            for child_name, child in device.child_list.items():
                walk_device(child)

        walk_device(connection_object)
        
        # 创建 GUI 控件 (BLACS 内置方法)
        # if ttls:
        #     self.create_digital_outputs(ttls)
        # if dacs:
        #     self.create_analog_outputs(dacs)
        # if ddss:
        #     self.create_dds_outputs(ddss)
            
        # 自动布局所有控件
        self.auto_place_widgets()


