import zmq
import json
import os
import subprocess
import time
import sys
import msvcrt

# 配置
WORKER_IP = "127.0.0.1" # Labscript 运行的 Windows 电脑 IP
PUB_PORT = 5555
SUB_PORT = 5556

def run_artiq_subscriber():
    ctx = zmq.Context()
    
    # 1. 订阅 Socket (连接到 Worker)
    print(f"Connecting to Worker at {WORKER_IP}:{PUB_PORT}...")
    sub_socket = ctx.socket(zmq.SUB)
    sub_socket.connect(f"tcp://{WORKER_IP}:{PUB_PORT}")
    # 使用 setsockopt_string 以便与测试脚本保持一致，并明确订阅的主题
    sub_socket.setsockopt_string(zmq.SUBSCRIBE, 'ARTIQ_EXECUTE') # 订阅执行消息
    sub_socket.setsockopt_string(zmq.SUBSCRIBE, 'ARTIQ_MANUAL')  # 订阅手动消息（实际上手动部分目前还未实现）
    
    # 2. 推送 Socket (发送反馈给 Worker)
    print(f"Connecting feedback to {WORKER_IP}:{SUB_PORT}...")
    push_socket = ctx.socket(zmq.PUSH)
    push_socket.connect(f"tcp://{WORKER_IP}:{SUB_PORT}")
    
    print(f"Subscriber started. Listening for ARTIQ commands from {WORKER_IP}...")
    print("Press 'q' or 'ESC' to exit when idle.")

    while True:
        try:
            # 实现退出功能，使用 poll 检查消息 (100ms 超时)，以便响应键盘输入
            if sub_socket.poll(100) == 0:
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    if key.lower() == b'q' or key == b'\x1b': # q 或 ESC
                        print("User requested exit. Stopping subscriber...")
                        break
                continue

            # 接收消息
            parts = sub_socket.recv_multipart()
            # 解码 topic，确保与 subscriber_test.py 一致
            topic = parts[0].decode('utf-8')
            msg_bytes = parts[1]
            data = json.loads(msg_bytes.decode('utf-8'))
            
            print(f"\n[Received] Topic: {topic}")
            
            # --- 1. 处理 PING 握手 ---
            if data.get('type') == 'PING':
                print("Received PING. Replying IDLE.")
                push_socket.send_json({'status': 'IDLE'})
                continue

            if topic == 'ARTIQ_EXECUTE':
                print("Received Experiment Script.")
                
                # 发送接收确认
                print("Sending ACK...")
                push_socket.send_json({'status': 'received'})
                
                # 增加一个小延时，确保 Worker 有时间处理 ACK
                time.sleep(0.1)
                
                # 发送一条立即的消息，确认 Worker 能收到后续消息
                push_socket.send_json({'status': 'print', 'message': 'Subscriber: ACK sent, preparing execution...'})
                
                script = data.get('script', '')
                if not script:
                    print("Error: No script content.")
                    push_socket.send_json({'status': 'error', 'message': 'No script content received'})
                    continue
                
                # 保存为临时文件
                # 使用绝对路径，避免路径问题
                filename = os.path.abspath(f"temp_experiment_{int(time.time())}.py")
                try:
                    with open(filename, 'w', encoding='utf-8') as f:
                        f.write(script)
                    
                    print(f"Created script at {filename}")
                    
                    # 调用 ARTIQ 执行
                    print(f"Executing artiq_run {filename}...")
                    
                    # 使用 Popen 实时获取输出
                    # 注意：需要确保 artiq_run 在 PATH 中
                    process = subprocess.Popen(
                        ["artiq_run", filename], 
                        stdout=subprocess.PIPE, 
                        stderr=subprocess.STDOUT, # 将 stderr 合并到 stdout
                        text=True,
                        bufsize=1, # 行缓冲
                        encoding='utf-8',
                        errors='replace',
                        cwd=os.path.dirname(filename) # 设置工作目录为脚本所在目录
                    )
                    
                    print("Subprocess started. Reading output...")
                    
                    # 实时读取输出并发送
                    while True:
                        line = process.stdout.readline()
                        if not line and process.poll() is not None:
                            break
                        if line:
                            line = line.strip()
                            print(f"[ARTIQ Log] {line}")
                            push_socket.send_json({'status': 'print', 'message': line})
                            
                    return_code = process.poll()
                    print(f"Subprocess finished with code {return_code}")
                    
                    if return_code == 0:
                        print("Execution Success.")
                        push_socket.send_json({'status': 'ok', 'message': 'Execution successful'})
                    else:
                        print(f"Execution Error (Code {return_code}).")
                        push_socket.send_json({'status': 'error', 'message': f"ARTIQ process exited with code {return_code}"})
                        
                except Exception as e:
                    print(f"Execution Exception: {e}")
                    import traceback
                    traceback.print_exc()
                    push_socket.send_json({'status': 'error', 'message': str(e)})
                finally:
                    # 清理临时文件
                    if os.path.exists(filename):
                        try:
                            os.remove(filename)
                            print(f"Removed temp file {filename}")
                        except Exception as e:
                            print(f"Failed to remove temp file: {e}")
                
            elif topic == 'ARTIQ_MANUAL':
                print(f"Received Manual Command: {data.get('values')}")
                # 这里需要编写代码调用 artiq_client 或 rpc 来实时改变 DDS/TTL
                # 例如调用 dashboard 的 RPC 接口
                pass

        except KeyboardInterrupt:
            print("Stopping subscriber...")
            break
        except Exception as e:
            print(f"Error in subscriber loop: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(1)

if __name__ == "__main__":
    # 允许通过命令行参数覆盖 IP
    if len(sys.argv) > 1:
        WORKER_IP = sys.argv[1]
    run_artiq_subscriber()