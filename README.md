# Labscript-ARTIQ Driver / Labscript-ARTIQ 驱动程序

[English](#english) | [中文](#中文)

---

<a name="english"></a>
## English

A device driver that integrates ARTIQ (Advanced Real-Time Infrastructure for Quantum physics) into the [Labscript](https://github.com/labscript-suite) experimental control framework. It translates Labscript's high-level timing language into native ARTIQ kernel scripts, enabling unified orchestration of ARTIQ hardware (Kasli-SoC, Fastino, Urukul, TTL) alongside other labscript-controlled devices within a single experiment.

### Architecture

```
User Experiment Script (labscript Python)
    │
    ▼
labscript_devices.py: ARTIQ_Master.generate_code()
    │  Computes timing for all child devices
    │  Aggregates TTL / DAC / DDS data
    │  Generates a complete ARTIQ kernel script
    │  Writes to HDF5: /devices/<name>/ARTIQ_SCRIPT
    ▼
BLACS Runtime
    │  ARTIQ_MasterTab: GUI & lifecycle management
    │  ARTIQ_Worker: ZMQ networking
    │     │  Handshake (PING/PONG)
    │     │  Broadcast script via PUB socket
    │     │  Poll execution status via PULL socket
    ▼
subscriber.py (ARTIQ machine)
    │  Receives script via ZMQ SUB
    │  Invokes artiq_run as subprocess
    │  Streams stdout/stderr back via ZMQ PUSH
    ▼
ARTIQ Hardware (Kasli-SoC, Fastino, Urukul, TTL)
```

### File Structure

| File | Purpose |
|------|---------|
| `labscript_devices.py` | Core code generator. Defines `ARTIQ_Master` (pseudoclock device), `ARTIQ_DDS` (Urukul adapter), and `_generate_artiq_script()` which translates labscript commands into a full ARTIQ experiment class. |
| `blacs_worker.py` | BLACS worker process. Manages ZMQ communication: handshake, script broadcasting, and execution status polling. |
| `blacs_tabs.py` | BLACS GUI tab. Provides the ARTIQ control panel in the BLACS interface and manages the worker lifecycle. |
| `subscriber.py` | Runs on the ARTIQ machine. Receives scripts via ZMQ, executes them with `artiq_run`, and streams output back to BLACS. |
| `register_classes.py` | Registers `ARTIQ_Master` with the labscript device registry. |
| `connection_table.py` | Example connection table showing how to configure ARTIQ devices. |
| `artiq_test_experiment.py` | Standalone test/example script demonstrating a complete labscript experiment with ARTIQ. |
| `artiq说明.md` | ARTIQ programming reference (Chinese). |
| `Labscript-ARTIQ 控制程序使用说明.md` | Full architecture and usage manual (Chinese). |

### Supported Hardware

- **TTL** — `DigitalOut`, connection: `ttl0`–`ttl39`
- **DAC** — `AnalogOut` via Fastino, connection: `fastino0_channelX` (X = 0–31)
- **DDS** — `ARTIQ_DDS` via Urukul, connection: `urukulX_chY` (X = 0–2, Y = 0–3)

### Quick Start

#### 1. Installation

Place the project folder under labscript's user device directory:

```
<labscript-suite>/userlib/user_devices/ARTIQ/
```

#### 2. Subscriber (ARTIQ machine)

On the machine connected to ARTIQ hardware, in the `artiq_master` directory:

```cmd
conda activate artiq
python subscriber.py [<worker_ip>]
```

The subscriber listens for scripts on port 5555 and sends feedback on port 5556 by default. Press `q` or `ESC` to exit when idle.

#### 3. Connection Table

In your labscript `connection_table.py`:

```python
from user_devices.ARTIQ.labscript_devices import ARTIQ_Master, ARTIQ_DDS

ARTIQ = ARTIQ_Master(
    name='ARTIQ',
    artiq_ip='127.0.0.1',
    broadcast_port=5555,
    feedback_port=5556,
)

# TTL
ttl0 = DigitalOut(name='ttl0', parent_device=ARTIQ.outputs, connection='ttl0')

# DAC
dac0 = AnalogOut(name='dac0', parent_device=ARTIQ.outputs, connection='fastino0_channel0')

# DDS
urukul0_ch0 = ARTIQ_DDS(name='urukul0_ch0', parent_device=ARTIQ.outputs, connection='urukul0_ch0')
```

#### 4. Experiment Script

```python
import runpy
from pathlib import Path
from labscript import *

# Load connection table
CONNECTION_TABLE_PATH = Path(r'<path_to_your_connection_table.py>')
ct = runpy.run_path(str(CONNECTION_TABLE_PATH))
for name, val in ct.items():
    if not name.startswith('__'):
        globals()[name] = val

start()

t = 0.0
ttl0.go_high(t)
t += 50*ms
ttl0.go_low(t)

dac0.ramp(t, duration=100*ms, initial=0.0, final=1.0, samplerate=10*kHz)

urukul0_ch0.set(t, frequency=80*MHz, amplitude=0.5, phase=0.0)
urukul0_ch0.set_att(t, 10.0)

stop(t + 100*ms)
```

### DDS Usage

Legacy labscript syntax is supported:

```python
urukul0_ch0.setfreq(t, 80 * MHz)
urukul0_ch0.setamp(t, 0.5)
urukul0_ch0.setphase(t, 0.0)
```

A combined `set()` method is also available:

```python
urukul0_ch0.set(t, frequency=80*MHz, amplitude=0.5, phase=0.0, phase_mode=1)
```

`phase_mode`: `0` = continuous, `1` = absolute, `2` = tracking.

Attenuation control (in dB):

```python
urukul0_ch0.set_att(t, 10.0)  # 0 to 31.5 dB
```

### Dependencies

- labscript / BLACS / labscript-utils
- ARTIQ (with `artiq_run` on the execution machine)
- pyzmq
- numpy
- h5py
- PyQt5 / qtutils (for BLACS GUI)

### Important Notes

- DAC synchronization uses the manual hold/write/update pattern. Each voltage change requires ~6 lines of ARTIQ code. For high-rate continuous waveforms (e.g., 10 kHz over seconds), the generated script may become too large for ARTIQ to compile. Consider future DMA support for such use cases.
- A 1 ms delay is inserted before the first operation to allow DAC setup time.
- DDS amplitude is relative (0 to 1.0, fraction of maximum output).
- `set_att()` sets attenuation in dB, not amplitude.
- Ensure IP addresses and ports match across `connection_table.py`, experiment scripts, and `subscriber.py`.
- Manual control via BLACS GUI is currently not implemented — submit scripts through the buffered run mode.

---

<a name="中文"></a>
## 中文

将 ARTIQ 实验控制系统集成到 [Labscript](https://github.com/labscript-suite) 实验控制框架中的设备驱动程序。它将 Labscript 的高级时序语言转译为原生 ARTIQ 内核脚本，实现在单一实验中对 ARTIQ 硬件（Kasli-SoC、Fastino、Urukul、TTL）与其他 labscript 控制设备的统一编排。

### 架构

```
用户实验脚本 (labscript Python)
    │
    ▼
labscript_devices.py: ARTIQ_Master.generate_code()
    │  计算所有子设备时序
    │  聚合 TTL / DAC / DDS 数据
    │  生成完整的 ARTIQ 内核脚本
    │  写入 HDF5: /devices/<name>/ARTIQ_SCRIPT
    ▼
BLACS 运行环境
    │  ARTIQ_MasterTab: GUI 与生命周期管理
    │  ARTIQ_Worker: ZMQ 网络通信
    │     │  握手检查 (PING/PONG)
    │     │  通过 PUB socket 广播脚本
    │     │  通过 PULL socket 轮询执行状态
    ▼
subscriber.py (ARTIQ 端计算机)
    │  通过 ZMQ SUB 接收脚本
    │  调用 artiq_run 子进程执行
    │  通过 ZMQ PUSH 实时回传 stdout/stderr
    ▼
ARTIQ 硬件 (Kasli-SoC, Fastino, Urukul, TTL)
```

### 文件结构

| 文件 | 功能 |
|------|------|
| `labscript_devices.py` | 核心代码生成器。定义 `ARTIQ_Master`（伪时钟设备）、`ARTIQ_DDS`（Urukul适配器），以及将 labscript 命令转译为完整 ARTIQ 实验类的 `_generate_artiq_script()`。 |
| `blacs_worker.py` | BLACS 工作进程。管理 ZMQ 通信：握手、脚本广播和执行状态轮询。 |
| `blacs_tabs.py` | BLACS GUI 选项卡。在 BLACS 界面中提供 ARTIQ 控制面板并管理工作进程生命周期。 |
| `subscriber.py` | 运行在 ARTIQ 端计算机上。通过 ZMQ 接收脚本，调用 `artiq_run` 执行，并将输出流式回传至 BLACS。 |
| `register_classes.py` | 向 labscript 设备注册表注册 `ARTIQ_Master`。 |
| `connection_table.py` | 示例连接表，展示如何配置 ARTIQ 设备。 |
| `artiq_test_experiment.py` | 独立的测试/示例脚本，演示完整的 labscript + ARTIQ 实验。 |
| `artiq说明.md` | ARTIQ 编程参考指南。 |
| `Labscript-ARTIQ 控制程序使用说明.md` | 完整架构与使用手册。 |

### 支持的硬件

- **TTL** — `DigitalOut`，连接属性：`ttl0`–`ttl39`
- **DAC** — `AnalogOut`（Fastino），连接属性：`fastino0_channelX`（X = 0–31）
- **DDS** — `ARTIQ_DDS`（Urukul），连接属性：`urukulX_chY`（X = 0–2, Y = 0–3）

### 快速开始

#### 1. 安装

将项目文件夹放置在 labscript 的用户设备目录下：

```
<labscript-suite>/userlib/user_devices/ARTIQ/
```

#### 2. 接收方（ARTIQ 端）

在连接 ARTIQ 硬件的计算机上，进入 `artiq_master` 目录：

```cmd
conda activate artiq
python subscriber.py [<worker_ip>]
```

默认监听端口 5555 接收脚本，端口 5556 发送反馈。空闲时按 `q` 或 `ESC` 退出。

#### 3. 连接表

在 labscript 的 `connection_table.py` 中：

```python
from user_devices.ARTIQ.labscript_devices import ARTIQ_Master, ARTIQ_DDS

ARTIQ = ARTIQ_Master(
    name='ARTIQ',
    artiq_ip='127.0.0.1',
    broadcast_port=5555,
    feedback_port=5556,
)

# TTL
ttl0 = DigitalOut(name='ttl0', parent_device=ARTIQ.outputs, connection='ttl0')

# DAC
dac0 = AnalogOut(name='dac0', parent_device=ARTIQ.outputs, connection='fastino0_channel0')

# DDS
urukul0_ch0 = ARTIQ_DDS(name='urukul0_ch0', parent_device=ARTIQ.outputs, connection='urukul0_ch0')
```

#### 4. 实验脚本

```python
import runpy
from pathlib import Path
from labscript import *

# 加载连接表
CONNECTION_TABLE_PATH = Path(r'<你的连接表路径>')
ct = runpy.run_path(str(CONNECTION_TABLE_PATH))
for name, val in ct.items():
    if not name.startswith('__'):
        globals()[name] = val

start()

t = 0.0
ttl0.go_high(t)
t += 50*ms
ttl0.go_low(t)

dac0.ramp(t, duration=100*ms, initial=0.0, final=1.0, samplerate=10*kHz)

urukul0_ch0.set(t, frequency=80*MHz, amplitude=0.5, phase=0.0)
urukul0_ch0.set_att(t, 10.0)

stop(t + 100*ms)
```

### DDS 用法

支持 Labscript 标准写法：

```python
urukul0_ch0.setfreq(t, 80 * MHz)
urukul0_ch0.setamp(t, 0.5)
urukul0_ch0.setphase(t, 0.0)
```

也支持更接近 ARTIQ 的合并写法：

```python
urukul0_ch0.set(t, frequency=80*MHz, amplitude=0.5, phase=0.0, phase_mode=1)
```

`phase_mode`：`0` = 连续模式，`1` = 绝对模式，`2` = 跟踪模式。

衰减设置（单位 dB）：

```python
urukul0_ch0.set_att(t, 10.0)  # 范围 0 到 31.5 dB
```

### 依赖

- labscript / BLACS / labscript-utils
- ARTIQ（执行端需要 `artiq_run`）
- pyzmq
- numpy
- h5py
- PyQt5 / qtutils（BLACS GUI 需要）

### 注意事项

- DAC 多通道同步采用手动 hold/write/update 模式，每次电压变化约需 6 行代码。若长时间高频更新（如 10 kHz 持续数秒），生成的脚本可能过大导致 ARTIQ 无法编译。未来可考虑加入 DMA 录制功能。
- 脚本默认在实验开始前延迟 1 ms，确保 DAC 设置时间。
- DDS 幅度为相对值（0–1.0，满幅输出的比例）。
- `set_att()` 设置的是衰减值（dB），不是输出幅度。
- 确保 `connection_table.py`、实验脚本和 `subscriber.py` 中的 IP 地址和端口一致。
- BLACS GUI 手动操控目前无法实现，请通过 buffered 模式提交脚本运行。
