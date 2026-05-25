import numpy as np
import h5py
import re
from labscript import (
    PseudoclockDevice, Pseudoclock, ClockLine, 
    IntermediateDevice, DigitalOut, AnalogOut, DDS, AnalogQuantity,
    config, LabscriptError, set_passed_properties
)
from labscript_devices import labscript_device, register_classes

# ------------------------------------------------------------------
# 辅助类定义
# ------------------------------------------------------------------

class ARTIQ_Pseudoclock(Pseudoclock):
    """内部伪时钟，负责收集时序"""
    pass

class ARTIQ_OutputBank(IntermediateDevice):
    """ARTIQ 输出通道容器"""
    allowed_children = [DigitalOut, AnalogOut, DDS]

class ARTIQ_DDS(DDS):
    """
    适配 ARTIQ Urukul 的 DDS 类
    connection_table.py和实验脚本中需要使用ARTIQ_DDS类
    """
    def __init__(self, name, parent_device, connection, table_mode=False, **kwargs):
        # ARTIQ Urukul 通常不需要 table_mode，这里保留接口
        DDS.__init__(self, name, parent_device, connection, **kwargs)
        self.attenuation = AnalogQuantity(
            f"{self.name}_att",
            self,
            "att",
            (0, 31.5),
            None,
            None,
        )
        self.phase_mode = AnalogQuantity(
            f"{self.name}_phase_mode",
            self,
            "phase_mode",
            (0, 2),
            None,
            None,
        )
        self._uses_phase_mode = False
        self._explicit_set_times = set()
        self._explicit_att_times = set()
        self._explicit_phase_mode_times = set()

    def set(
        self,
        t,
        frequency=None,
        amplitude=None,
        phase=None,
        phase_mode=None,
        frequency_units=None,
        amplitude_units=None,
        phase_units=None,
    ):
        """Set ARTIQ DDS frequency, amplitude, and phase at the same labscript time."""
        if frequency is not None or amplitude is not None or phase is not None:
            self._explicit_set_times.add(round(t, 10))
        if frequency is not None:
            self.setfreq(t, frequency, frequency_units)
        if amplitude is not None:
            self.setamp(t, amplitude, amplitude_units)
        if phase is not None:
            self.setphase(t, phase, phase_units)
        if phase_mode is not None:
            phase_mode = int(phase_mode)
            if phase_mode not in (0, 1, 2):
                raise LabscriptError(
                    f"ARTIQ DDS phase_mode must be 0, 1, or 2, got {phase_mode}"
                )
            self._uses_phase_mode = True
            self._explicit_phase_mode_times.add(round(t, 10))
            self.phase_mode.constant(t, phase_mode)

    def set_att(self, t, value, units=None):
        """Set the ARTIQ Urukul output attenuation in dB."""
        self._explicit_att_times.add(round(t, 10))
        self.attenuation.constant(t, value, units)

# ------------------------------------------------------------------
# 主设备类
# ------------------------------------------------------------------


class ARTIQ_Master(PseudoclockDevice):
    """
    ARTIQ Kasli-SoC 主控设备类，用于生成 ARTIQ 实验脚本\\
    ARTIQ下属输出通道通过 outputs 属性访问（parent_device=ARTIQ_Master_name.outputs）\\
    TTL 设备使用 DigitalOut，connection 属性为 'ttlX'\\
    DAC 设备使用 AnalogOut ，connection 属性为 'channel0', 'fastino0_channelX', 'dac0'等，只看末位数字，代表通道索引\\
    DDS 设备使用 ARTIQ_DDS ，connection 属性为 'urukul0_chX'
    """
    description = 'ARTIQ Kasli-SoC Master Controller'
    clock_limit = 1.25e8        # 125 MHz (系统时钟)
    clock_resolution = 8e-9  # 8 ns
    allowed_children = [ARTIQ_Pseudoclock]

    @set_passed_properties(
        property_names={
            "connection_table_properties": ["artiq_ip", "broadcast_port", "feedback_port"]
        }
    )
    def __init__(self, name, artiq_ip="127.0.0.1", broadcast_port=5555, feedback_port=5556, **kwargs):
        # 作为 Master，trigger_device 必须为 None
        PseudoclockDevice.__init__(self, name, trigger_device=None, **kwargs)
        
        self.artiq_ip = artiq_ip
        self.broadcast_port = broadcast_port
        self.feedback_port = feedback_port
        self.BLACS_connection = f'{artiq_ip},{broadcast_port},{feedback_port}'
        
        # 1. 内部架构：Pseudoclock -> ClockLine
        self.pseudoclock = ARTIQ_Pseudoclock(f'{name}_pseudoclock', self, 'internal')
        self.clockline = ClockLine(f'{name}_clockline', self.pseudoclock, 'internal')
        
        # 2. 输出容器：IntermediateDevice
        # Labscript 不允许直接将 DigitalOut 挂载到 Generic ClockLine，必须通过 IntermediateDevice
        self.outputs = ARTIQ_OutputBank(f'{name}_outputs', self.clockline)

    def generate_code(self, hdf5_file):
        # 1. 让 Labscript 计算所有子设备的时序
        PseudoclockDevice.generate_code(self, hdf5_file)
        
        # 2. 提取编译后的数据
        times = self.pseudoclock.times  # 时间点数组 (seconds)
        
        # 处理 times 可能是字典的情况
        # 在某些 Labscript 版本中，times 是一个字典 {clockline: times_array}
        if isinstance(times, dict):
            if self.clockline in times:
                times = times[self.clockline]
            else:
                # Fallback: 取第一个值
                times = list(times.values())[0]
        
        # 获取挂载的设备
        # 从 outputs 容器中获取子设备
        child_devices = self.outputs.child_devices
        
        # 分类设备以便处理
        ttls = [d for d in child_devices if isinstance(d, DigitalOut)]
        dacs = [d for d in child_devices if isinstance(d, AnalogOut)] # Fastino
        ddss = [d for d in child_devices if isinstance(d, DDS)]       # Urukul
        
        # --- 手动聚合 DDS 数据 ---
        # Labscript 将 DDS 数据分散在 frequency/amplitude/phase 子对象中
        # 需要将其合并，以便 _generate_artiq_script 可以通过 dds.raw_output[i] 访问
        for dds in ddss:
            dtype = [('freq', float), ('amp', float), ('phase', float)]
            if hasattr(dds, 'attenuation'):
                dtype.append(('att', float))
            if hasattr(dds, 'phase_mode'):
                dtype.append(('phase_mode', float))
            structured_data = np.zeros(len(times), dtype=dtype)
            
            # 辅助函数：确保数据长度与 times 一致
            # 解决可能出现的 shape mismatch 问题 (e.g. (2,) vs (1,))
            def match_len(arr, target_len):
                if len(arr) == target_len:
                    return arr
                elif len(arr) > target_len:
                    # 【关键解释】
                    # 如果 arr 比 times 长，通常是因为 arr 包含了 stop_time 的值。
                    # 由于 Labscript 数据是 Zero-Order Hold (零阶保持) 且起点对齐，
                    # arr[i] 对应 times[i]。因此直接截取前 target_len 个数据是物理上正确的，
                    # 丢弃的是实验结束后的状态值。
                    return arr[:target_len]
                else:
                    # 如果数据不够，用最后一个值填充 (Zero-Order Hold)
                    # 这通常发生在静态输出或默认值情况下
                    padding = np.full(target_len - len(arr), arr[-1] if len(arr) > 0 else 0)
                    return np.concatenate((arr, padding))

            structured_data['freq'] = match_len(dds.frequency.raw_output, len(times))
            structured_data['amp'] = match_len(dds.amplitude.raw_output, len(times))
            structured_data['phase'] = match_len(dds.phase.raw_output, len(times))
            if 'att' in structured_data.dtype.names:
                structured_data['att'] = match_len(dds.attenuation.raw_output, len(times))
            if 'phase_mode' in structured_data.dtype.names:
                structured_data['phase_mode'] = match_len(dds.phase_mode.raw_output, len(times))
            
            dds.raw_output = structured_data

        # 3. 生成 ARTIQ Python 脚本
        script_text = self._generate_artiq_script(times, ttls, dacs, ddss)
        
        # 4. 写入 HDF5
        group = self.init_device_group(hdf5_file)
        dt = h5py.special_dtype(vlen=str)
        dset = group.create_dataset('ARTIQ_SCRIPT', (1,), dtype=dt)
        dset[0] = script_text
        timeline_start_s, shot_end_s = self._estimate_timing_window(times)
        self._apply_compiled_timing_metadata(group, timeline_start_s, shot_end_s)
        
        print(f"[ARTIQ] Generated kernel script for {self.name}")

    def _estimate_timing_window(self, times):
        if len(times):
            timeline_start = float(times[0])
            latest_point = float(times[-1])
        else:
            timeline_start = 0.0
            latest_point = 0.0

        shot_end = max(float(getattr(self, 'stop_time', latest_point)), latest_point)
        return timeline_start, shot_end

    def _apply_compiled_timing_metadata(self, group, timeline_start_s, shot_end_s):
        timeline_start_s = float(timeline_start_s)
        shot_end_s = float(shot_end_s)
        if shot_end_s < timeline_start_s:
            shot_end_s = timeline_start_s

        group.attrs['t_start'] = timeline_start_s
        self.set_property(
            'stop_time',
            float(shot_end_s - timeline_start_s),
            location='device_properties',
        )

    def _generate_artiq_script(self, times, ttls, dacs, ddss):
        """
        生成完整的 ARTIQ 实验脚本字符串
        参数:
            times: 时间点数组 (seconds)
            ttls: DigitalOut 设备列表，connection 属性为 “ttlX”
            dacs: AnalogOut 设备列表 (Fastino)，connection 属性为 “channel0”， “fastino0_channelX”, “dac0”等，只看末位数字，代表通道索引
            ddss: DDS 设备列表 (Urukul)，connection 属性为 “urukul0_chX”
        """
        
        # 现有的文件需要Labscript先读取ARTIQ设备及子设备时序后，生成h5文件中时序，然后读取h5时序生成artiq脚本；因此还需要确认labscript读取时序生成h5文件的数据内容和格式是否与下方程序要求一致
        # --- 辅助函数：解析连接名称 ---
        # 这些函数根据设备的 connection 属性提取名称或索引，因此要求用户在连接表中正确命名设备（connection正确）
        def get_ttl_name(dev):
            # connection 如 'ttl0'
            return dev.connection
            
        def get_dds_name(dev):
            # connection 如 'urukul0_ch0'
            return dev.connection
            
        def get_dds_cpld_name(dev):
            # connection 如 'urukul0_ch0' -> 'urukul0_cpld'
            if 'urukul' in dev.connection:
                return dev.connection.split('_ch')[0] + '_cpld'
            return 'urukul0_cpld' # Fallback
            
        def get_dac_channel(dev):
            # 需要 connection 如 'channel0' 或 'fastino0_channel0'
            # 提取数字
            s = dev.connection.lower()
            # 简单提取末尾数字
            import re
            match = re.search(r'(\d+)$', s)
            if match:
                return int(match.group(1))
            return 0 # Fallback

        def format_float_literal(value, precision=12):
            """强制输出带小数点的浮点字面量，避免 ARTIQ 将整数常量推断为 int。"""
            try:
                v = float(value)
            except (TypeError, ValueError):
                raise LabscriptError(f"Invalid numeric value for ARTIQ script: {value}")

            if not np.isfinite(v):
                raise LabscriptError(f"Non-finite numeric value for ARTIQ script: {value}")

            s = format(v, f'.{precision}g')
            if 'e' in s or 'E' in s:
                base, exp = s.lower().split('e', 1)
                if '.' not in base:
                    base += '.0'
                return f'{base}e{exp}'

            if '.' not in s:
                s += '.0'

            return s

        # --- 脚本头部 ---
        lines = [
            "from artiq.experiment import *",
            "import numpy as np",
            "",
            "class LabscriptGenerated(EnvExperiment):",
            "    def build(self):",
            "        self.setattr_device('core')",
            "        self.setattr_device('core_dma')",
            "        self.setattr_device('fastino0')", # 始终注册 Fastino
        ]
        
        # 注册设备
        registered_devices = set(['core', 'core_dma', 'fastino0'])
        
        # 注册 TTL
        for dev in ttls:
            name = get_ttl_name(dev)
            if name not in registered_devices:
                lines.append(f"        self.setattr_device('{name}')")
                registered_devices.add(name)
                
        # 注册 DDS 及其 CPLD
        for dev in ddss:
            name = get_dds_name(dev)
            if name not in registered_devices:
                lines.append(f"        self.setattr_device('{name}')")
                registered_devices.add(name)
            
            cpld = get_dds_cpld_name(dev)
            if cpld not in registered_devices:
                lines.append(f"        self.setattr_device('{cpld}')")
                registered_devices.add(cpld)

        lines.append("")
        lines.append("    @kernel")
        lines.append("    def run(self):")
        lines.append("        self.core.reset()")
        
        # --- 初始化硬件 ---
        # Fastino Init
        lines.append("        self.fastino0.init()")
        
        # DDS Init
        processed_cplds = set()
        for dev in ddss:
            cpld = get_dds_cpld_name(dev)
            name = get_dds_name(dev)
            
            if cpld not in processed_cplds:
                lines.append(f"        self.{cpld}.init()")
                processed_cplds.add(cpld)
            
            lines.append(f"        self.{name}.init()")
            # 默认起始关闭 DDS 输出，等待后续设置
            lines.append(f"        self.{name}.sw.off()") 

        lines.append("")
        lines.append("        self.core.break_realtime()")
        lines.append("        delay(1*ms)  # 留出缓冲时间，确保第一个命令为修改DAC时不会出错")
        lines.append("        t0 = now_mu() # 记录实验开始的绝对时间")
        lines.append("")

        # --- 时序生成核心循环 ---
        
        # 转换时间为机器单位 (mu), 1ns = 1mu
        times_mu = np.array(times * 1e9, dtype=np.int64)
        
        # 遍历时间点
        for i, t_mu in enumerate(times_mu):
            
            # 1. 处理 Fastino (DAC) - 需要提前写入
            # -------------------------------------------------
            changing_dacs = []
            for dac in dacs:
                val = dac.raw_output[i]
                prev_val = dac.raw_output[i-1] if i > 0 else 0.0
                if val != prev_val:
                    changing_dacs.append((dac, val))
            
            if changing_dacs:
                # 预留时间，每个通道 10 mu（最少8ns），加上 100 mu 的固定开销保证安全
                pre_time_mu = len(changing_dacs) * 10 + 100 
                # 到达预留时间点（执行时需要预留时间，确保 DAC 写入完成）
                lines.append(f"        at_mu(t0 + {t_mu} - {pre_time_mu})")
                
                # Fastino Hold
                # 直接使用全部通道掩码 0xFFFFFFFF，也可以根据实际使用通道生成掩码
                lines.append(f"        self.fastino0.set_hold(0xFFFFFFFF)") 
                
                for dac, val in changing_dacs:
                    ch_idx = get_dac_channel(dac)
                    voltage_literal = format_float_literal(val)
                    lines.append(f"        delay(10*ns)")  # 每个通道写入间隔 10 mu
                    lines.append(
                        f"        self.fastino0.write({ch_idx}, self.fastino0.voltage_to_mu({voltage_literal}))"
                    )
            
            # 2. 执行同步更新 (TTL, DDS, DAC Update)
            # -------------------------------------------------
            # 收集当前时间点的所有指令，只有当确实有指令时才写入 at_mu
            current_cmds = []

            # A. Fastino Update
            if changing_dacs:
                current_cmds.append(f"        delay(10*ns)")
                current_cmds.append(f"        self.fastino0.update(0xFFFFFFFF)")
            
            # B. TTL Update
            for ttl in ttls:
                val = ttl.raw_output[i]
                prev_val = ttl.raw_output[i-1] if i > 0 else 0
                name = get_ttl_name(ttl)
                
                if val != prev_val and (i > 0 or val > 0):
                    if val > 0:
                        current_cmds.append(f"        self.{name}.on()")
                    else:
                        current_cmds.append(f"        self.{name}.off()")
            
            # C. DDS Update
            for dds in ddss:
                val = dds.raw_output[i]
                prev_val = dds.raw_output[i-1] if i > 0 else np.zeros(1, dtype=dds.raw_output.dtype)[0]
                name = get_dds_name(dds)

                try:
                    current_time = round(float(times[i]), 10)
                    explicit_set_times = getattr(dds, '_explicit_set_times', set())
                    explicit_att_times = getattr(dds, '_explicit_att_times', set())
                    explicit_phase_mode_times = getattr(dds, '_explicit_phase_mode_times', set())
                    dtype_names = dds.raw_output.dtype.names or ()
                    waveform_changed = (
                        val['freq'] != prev_val['freq']
                        or val['amp'] != prev_val['amp']
                        or val['phase'] != prev_val['phase']
                        or current_time in explicit_set_times
                    )
                    phase_mode_changed = (
                        'phase_mode' in dtype_names
                        and (
                            val['phase_mode'] != prev_val['phase_mode']
                            or current_time in explicit_phase_mode_times
                        )
                    )
                    att_changed = (
                        'att' in dtype_names
                        and (
                            val['att'] != prev_val['att']
                            or current_time in explicit_att_times
                        )
                    )

                    if att_changed:
                        att_literal = format_float_literal(val['att'])
                        current_cmds.append(f"        self.{name}.set_att({att_literal}*dB)")

                    if waveform_changed or phase_mode_changed:
                        freq_literal = format_float_literal(val['freq'])
                        phase_literal = format_float_literal(val['phase'])
                        amp_literal = format_float_literal(val['amp'])
                        set_args = (
                            f"frequency={freq_literal}, "
                            f"phase={phase_literal}, "
                            f"amplitude={amp_literal}"
                        )
                        if 'phase_mode' in dtype_names and getattr(dds, '_uses_phase_mode', False):
                            phase_mode_value = int(round(float(val['phase_mode'])))
                            set_args += f", phase_mode={phase_mode_value}"
                        current_cmds.append(f"        self.{name}.set({set_args})")
                        current_cmds.append(f"        self.{name}.sw.on()")
                except (KeyError, TypeError, ValueError) as exc:
                    raise LabscriptError(
                        f"Failed to generate ARTIQ DDS command for {dds.name} "
                        f"at t={float(times[i])}: {exc}"
                    )
            
            # 只有当有指令需要执行时，才移动时间光标并写入指令
            if current_cmds:
                lines.append(f"        at_mu(t0 + {t_mu})")
                lines.extend(current_cmds) 

        # --- 脚本收尾 ---
        lines.append("")
        lines.append("        self.core.wait_until_mu(now_mu())")
        lines.append("        delay(10*ms)  # 必须有一段延迟，否则会触发RTIOUnderflow错误")
        
        # --- 安全复位 (DDS off, TTL low, DAC zero) ---
        lines.append("")
        lines.append("        # Safety cleanup")
        
        # 1. 关闭 DDS 输出
        for dds in ddss:
            name = get_dds_name(dds)
            lines.append(f"        delay(10*ns)")  # 每个 DDS 之间留出 10 mu 的间隔
            lines.append(f"        self.{name}.sw.off()")
            # lines.append(f"        delay(10*us)")  # 确保有足够时间关闭 DDS

        # 2. TTL 置低
        for ttl in ttls:
            name = get_ttl_name(ttl)
            lines.append(f"        delay(8*ns)")  # 每个 TTL 之间留出 10 mu 的间隔
            lines.append(f"        self.{name}.off()")
            

        # 3. DAC 置零 (Fastino)
        if dacs:
            used_dac_indices = set()
            for dac in dacs:
                used_dac_indices.add(get_dac_channel(dac))
            
            if used_dac_indices:
                lines.append(f"        self.fastino0.set_hold(0xFFFFFFFF)")
                for ch_idx in used_dac_indices:
                    lines.append(f"        delay(10*ns)")
                    lines.append(
                        f"        self.fastino0.write({ch_idx}, self.fastino0.voltage_to_mu({format_float_literal(0.0)}))"
                    )
                lines.append(f"        delay(10*ns)")
                lines.append(f"        self.fastino0.update(0xFFFFFFFF)")

        lines.append("        print('Experiment complete.')")
        lines.append("        self.core.reset()") # 实验结束后复位是个好习惯
        
        return "\n".join(lines)


