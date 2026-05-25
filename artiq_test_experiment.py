from labscript import *
import sys
import os

# Ensure we can import from the local ARTIQ folder
# Assuming this script is run from d:\Tsinghua\Ultracold\ARTIQ\控制程序
if os.getcwd() not in sys.path:
    sys.path.append(os.getcwd())

try:
    from user_devices.ARTIQ.labscript_devices import ARTIQ_Master
except ImportError:
    print("Could not import ARTIQ_Master from ARTIQ.labscript_devices.")
    print("Please ensure the 'ARTIQ' folder is in the same directory as this script.")
    sys.exit(1)

# Initialize the Master Clock (ARTIQ Kasli-SoC)
# artiq_ip: IP address of the ARTIQ system (or the machine running the subscriber if simulating)
# broadcast_port: Port to send the script to
ARTIQ_Master(name='ARTIQ', artiq_ip="127.0.0.1", broadcast_port=5555)

# --- Define Channels ---

# TTL Outputs
# Connection names must match what is expected by the ARTIQ gateware/configuration
DigitalOut(name='ttl0', parent_device=ARTIQ.outputs, connection='ttl0')
DigitalOut(name='ttl1', parent_device=ARTIQ.outputs, connection='ttl1')

# DDS Outputs (Urukul)
# Connection format: 'urukul{card_idx}_ch{channel_idx}'
# DDS(name='urukul0_ch0', parent_device=ARTIQ.outputs, connection='urukul0_ch0')
# DDS(name='urukul0_ch1', parent_device=ARTIQ.outputs, connection='urukul0_ch1')

# Analog Outputs (Fastino/Zotino)
# Connection format: 'channel{idx}' or 'fastino{idx}_channel{idx}' depending on implementation
# Based on labscript_devices.py logic: get_dac_channel extracts the number at the end.
AnalogOut(name='dac0', parent_device=ARTIQ.outputs, connection='fastino0_channel0')
AnalogOut(name='dac1', parent_device=ARTIQ.outputs, connection='fastino0_channel1')


# --- Experiment Sequence ---

start()

t = 0

# Initial States
ttl0.go_low(t)
# ttl1.go_low(t)
# fastino_ch0.constant(t, 0.0)
# fastino_ch1.constant(t, 0.0)

# urukul0_ch0.setfreq(t, 10*MHz)
# urukul0_ch0.setamp(t, 0.0)
# urukul0_ch0.setphase(t, 0.0)

# urukul0_ch1.setfreq(t, 20*MHz)
# urukul0_ch1.setamp(t, 0.0)

t += 100*ms

# 1. TTL Pulse Test
ttl0.go_high(t)
t += 50*ms
ttl0.go_low(t)

t += 50*ms

# 2. Analog Ramp Test (Fastino)
# Ramp from 0V to 1V over 100ms
# fastino_ch0.ramp(t, duration=100*ms, initial=0.0, final=1.0, samplerate=10*kHz)
# t += 100*ms
# fastino_ch0.constant(t, 0.0)

# t += 50*ms

# 3. DDS Frequency Sweep Test (Discrete steps)
# Labscript DDS ramps are often implemented as discrete steps if the hardware doesn't support hardware ramps
# ARTIQ_Master implementation in labscript_devices.py samples the DDS values at each time step.
# So we can just set values.

# urukul0_ch0.setamp(t, 0.5) # Turn on
# for i in range(10):
#     urukul0_ch0.setfreq(t, (10 + i)*MHz)
#     t += 10*ms

# urukul0_ch0.setamp(t, 0.0) # Turn off

# t += 100*ms

# 4. Parallel Operations
# ttl1.go_high(t)
# fastino_ch1.constant(t, 0.5)
# urukul0_ch1.setamp(t, 0.3)
# t += 50*ms
# ttl1.go_low(t)
# fastino_ch1.constant(t, 0.0)
# urukul0_ch1.setamp(t, 0.0)

# Stop the experiment
stop(t + 100*ms)
