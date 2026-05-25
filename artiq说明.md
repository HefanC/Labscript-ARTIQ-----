## 编写ARTIQ的Python控制程序

提交的Python程序由两部分组成：一部分在计算机上直接运行，而由@kernel修饰的部分会被ARTIQ程序自动编译为elf文件传送到ARTIQ设备的CPU上运行；两部分之间可以互相调用或传递参数等.

在ARTIQ设备CPU上运行的Python（或者说Python文件的kernel部分）不完全支持Python3的所有内容（例如，只支持静态数据结构，不支持堆数据结构，列表元素数据类型必须相同，长度不能改变等）；同时还包含了一些内置的函数. 具体问题比较复杂，遇到问题最好参考说明文档.

### 基础设置

ARTIQ控制程序有基本的框架，编写时需要遵循以下几点：

程序必须调用库：

```python
from artiq.experiment import *
```

通过类定义实验（也就是程序所要完成的一系列操作）；类的名称为实验名称，在GUI仪表盘上显示的就是实验名称（而不是py文件名称）. 类中必须含有build()方法和@kernel修饰的run()方法，前者在计算机上运行，提前准备硬件信息、实验所需参数等；后者在设备上运行，执行实验操作；也可以添加其他方法.

```python
from artiq.experiment import *

class Experiment(EnvExperiment):     
# Experiment类继承自EnvExperiment，几乎所有实验都应继承自该类
    def build(self):
		    self.setattr_device("core")    # 调用核心（Kasli-SoC）
		    self.setattr_device("core_dma")    # 调用核心的DMA模块
		    self.setattr_device("ttl0")    # 调用ttl0，以下可直接用self.ttl0表示
		    self.setattr_device("fastino0")    # 调用Fastino
		    self.ttl = self.get_device("ttl1")    # 同样调用ttl1，但手动给了名称ttl
		    
		    self.parameter = self.get_device(key, processor)
		    self.setattr_argument(key, processor)
		    # key="name"，为参数名称；processor为描述参数的类，下方详细介绍
		    # get_和setattr_两种方法除了名称外，还可能有细微的不同，遇到bug再说吧
    
    @kernel    # kernel修饰代表这部分代码在设备CPU上运行
    def run(self):
        self.core.reset()    # 初始化Kasli-SoC，一般都要有
        ......
        
        self.core.break_realtime()    # 重置时间游标，设置125000个机器单位的余量
        
        delay(...)    # 保持...时间（SI单位制）——推后时间游标
        delay_mu(...)    # 机器单位（1mu = 1ns）
        
        now_mu()    # 读取时间游标
        at_mu(...)    # 将时间游标设置在...
        
        with parallel:    # 以下的各行语句将同时执行（只对一层生效，进一步缩进的不生效）
            ...
            ...
        with sequential:    # 以下的各行语句将顺序执行（只对一层生效）
            ...
            ...
        
        with self.interactive(title="parameter_title") as interactive:
            interactive.setattr_argument(key, processor)
        parameter = interactive.parameter_title
        # 在实验运行过程中输入的交互式参数，需要使用with代码块
        # 实验中可在GUI右上的“Interactive Args”窗口输入
```

注意：

- with parallel会将下方block中的语句包含的事件打上相同的事件戳，即时间游标重置回原来的时刻，now_mu在并行块中的每个语句开始时都会重置，但墙钟时间会继续前进；如果某个语句的执行时间过长（这与该语句调度的事件需要的时间长短无关！），墙钟时间可能会超过重置值，导致并行块中的后续语句处于负余量状态.

- get_argument()和setattr_argument()可接受Bool值、数值和其他参数形式，需要在processor部分注明：

  ```python
  self.setattr_argument("repeat", BooleanValue(True))
  self.setattr_argument("frequency", \
  											NumberValue(1.0, min=0.1, max=1000.0, step=1, unit="kHz"))
  ```

  其中NumberValue可以设置以下参数：

  ```python
  NumberValue(default, unit=, scale=, step=, min=, max=, precision=, type=)
  # default: 参数默认值
  # unit: 表示参数的单位
  # scale: 缩放因子，在实验中引用参数值时，需乘以缩放因子
  # step: UI中上下按钮修改参数值时使用的步长，默认值为缩放因子除以10
  # min: 参数的最小值
  # max: 参数的最大值
  # precision: UI使用的最大小数位数
  # type: 该数字的类型，接受“float”、“int”或“auto”，默认为“auto”
  ```

  当设置了参数后，在实验执行时在初始界面输入参数.

### TTL控制程序

在build()方法中引入TTL输出端口：

```python
# 第一种方法，赋予的名称为self.ttl
self.ttl = self.get_device("ttl0") #"ttl0"为端口名称，本设备上可以为ttl0~39
# 第二种方法，赋予的名称为self.ttl0（硬件原名称）
self.setattr_device("ttl0")
```

run()方法（或其他）设置TTL输出高/低电平（或者说打开/关闭TTL），输出脉冲：

```python
self.ttl.on()
self.ttl.off()

self.ttl.pulse(5*ns)    # 输出持续5ns的脉冲
```

### DAC控制程序

build()方法中调用Fastino：

```python
self.setattr_device("fastino0")    # 或者同上用get_device()也行
```

运行DAC输出：

```python
self.fastino0.init()    # 运行时run()方法中最开始需要先初始化Fastino

self.fastino0.set_dac(channel, voltage)    # 在channel通道设置电压voltage
self.fastino0.set_dac_mu(channel, voltage_mu)    # voltage为机器单位
```

datasheet上写DAC输出电压范围为-10~10V，但实际设置电压为10V时可能遇到问题.

DAC多通道同步输出较为麻烦，设备默认一次只能设置一个channel（修改设定操作较为复杂），但同一时间戳下设置两个channel又会引发collision错误（只要Fastino做两次操作就会），因此Fastino的两次操作之间需要加入delay.

```python
self.fastino0.set_hold(...)
# 将通道设置为手动更新，保证不同通道能同时更新
# hold()输入参数为通道位掩码，即二进制下0代表不选择该通道，1代表选择
# 如选择通道0和1，位掩码为3
# 选择0，2，5，位掩码mask = (1 << 0) | (1 << 2) | (1 << 5)（或用求和）
delay(8*ns)    # 一个粗时间戳为8ns，所以至少delay(8*ns)一般可以有效避免collision
self.fastino0.set_dac(channel1, voltage1)
voltage1_mu = self.fastino0.voltage_to_mu(voltage1)
self.fastino0.write(channel1, voltage1_mu)
# 这里用set_dac和write都行，write更快，但write接受的电压参数只能是机器单位，如上所示
delay(8*ns)
# 每两个fastino的操作之间都需要delay，不然同时提交两个命令到一个通道（fastino）会collision
self.fastino0.write(channel2, voltage2_mu)
delay(8*ns)
...
self.fastino0.update(...)
# 更新通道输出，update接受的输入参数同样是要更新的通道的位掩码
```

这样做显而易见的问题是每个set_dac和update都是单独的命令，需要设备CPU处理，因此当需要频繁更新多个通道时容易因CPU处理速度跟不上而导致RTIO Underflow错误.

Fastino也有方法set_group，可以在一个命令中同时设置一组通道的电压值，而一组通道的个数由设备的参数log2_width定义. 当log2_width=0，只能使用set_dac，不能使用set_group；而当log2_width=1，2，3，4，5，分别代表一组有2，4，8，16，32个通道. log2_width的设置在device_db.py文件中，且必须与硬件gateware（应该是Kasli-SoC上）保持一致，如需更改还需要更改Kasli-SoC的配置，较为复杂. 遗憾的是，实验室的ARTIQ中Fastino设置的log2_width=0，不能使用set_group.

[set_dac() to set multiple DACs in datasheet of Fastino - M-Labs Forum](https://forum.m-labs.hk/d/1002-set-dac-to-set-multiple-dacs-in-datasheet-of-fastino)

### DDS控制程序

DDS需要在build()中调用的多一点，有urukul_cpld（时钟）和urukul_ch（输出通道）两部分.

```python
self.cpld0 = self.get_device("urukul0_cpld")    # urukul0_cpld代表第一张DDS板卡的时钟部分
self.dds0 = self.get_device("urukul0_ch0")    # urukul0_ch0代表第一张板卡的第一个输出通道
```

DDS输出：

```python
self.cpld0.init()
self.dds0.init()    # 同样两部分都需要先初始化

self.dds0.set_att(...*dB)
# 设置振幅衰减值（0~31.5dB），或者说应该是设置最大振幅；但最大振幅还和频率有关
self.dds0.set(frequency=...float, phase=...float, amplitude=...float, phase_mode=...)
# 设置输出波形（但不输出）
# 频率最大为400MHz，幅度是以最大输出电压为单位（因此最大为1），phase为相位偏置
# phase和amplitude必须为float数据类型，即整数必须加.0
self.dds0.sw.on()    # 开始DDS输出
self.dds0.sw.off()    # 关闭DDS输出

# phase mode有三种：
# phase_mode=0：PHASE_MODE_CONTINUOUS改变频率时不清零相位
# phase_mode=1：PHASE_MODE_ABSOLUTE清零相位
# phase_mode=2：PHASE_MODE_TRACKING清零相位，但再叠加一段从初始到此刻以新频率运行的相位
```

DDS还可以利用自带的RAM，将需要输出的波形提前储存，之后快速播放.

### 使用DMA

DMA允许将固定的RTIO事件序列存储在系统内存中，并由FPGA中的DMA核心以高速播放这些序列；一旦记录，事件序列即为固定且不可修改，但可在时间线任意位置安全快速地重复回放，也可多次循环执行.

一个使用DMA输出TTL方波的实验程序：

```python
from artiq.experiment import *

class DMAPulses(EnvExperiment):
    def build(self):
        self.setattr_device("core")
        self.setattr_device("core_dma")    # 调用DMA
        self.setattr_device("ttl0")

    @kernel
    def record(self):    # 将一系列操作命令写入内存，后续可随时快速播放
        with self.core_dma.record("pulses"):    # 记录的波形名称“pulse”
            # all RTIO operations now_mu go to the "pulses"
            # DMA buffer, instead of being executed immediately.
            for i in range(50):
                self.ttl0.pulse(100*ns)
                delay(100*ns)

    @kernel
    def run(self):
        self.core.reset()
        self.record()    # 执行录入操作的命令
        # prefetch the address of the DMA buffer
        # for faster playback trigger
        pulses_handle = self.core_dma.get_handle("pulses")    # 调取内存中储存的波形
        self.core.break_realtime()    # 重置时间游标，清楚之前造成的正slack消耗
        while True:
            # execute RTIO operations in the DMA buffer
            # each playback advances the timeline by 50*(100+100) ns
            self.core_dma.playback_handle(pulses_handle)    # 播放内存中的波形
```

## 编写整体控制程序

build

将不同输出端口导入并命名；选择端口时可考虑一下串扰和延迟问题

DAC输出端口无法直接导入，可以先将不同命名设定为对应通道的序号

run

初始化，`core.reset()`，`fastino.init()`，`cpld.init()`，`dds.init()`

实验时序开始前`core.break_realtime()`一下，重置正slack

DAC多通道输出，先在实验时序开始前`set_hold`通道，在需要在输出改变的节点前一个一个（中间加`delay`）`write`对应通道的电压，在更新时刻`update`

其他的输出改变只需要在对应时刻执行对应操作

使用`t_mu`控制时间线而不是`delay`——DAC操作需要预先频繁`delay`，这样可以避免减去预先`delay`的时间

