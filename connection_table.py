from labscript import *
from labscript_devices import *
from labscript_devices.DummyPseudoclock.labscript_devices import DummyPseudoclock
from labscript_devices.DummyIntermediateDevice import DummyIntermediateDevice

# # Use a virtual, or 'dummy', device for the psuedoclock
# DummyPseudoclock(name='pseudoclock')

# # An output of this DummyPseudoclock isoutputs' attribute, which we use
# # to trigger children devices
# DummyIntermediateDevice(name='intermediate_device', parent_device=pseudooutputs)

# # Create an AnalogOut child of the DummyIntermediateDevice
# AnalogOut(name='analog_out', parent_device=intermediate_device, connection='ao0')

# # Create a DigitalOut child of the DummyIntermediateDevice
# DigitalOut(
#     name='digital_out', parent_device=intermediate_device, connection='port0/line0'
# )

# from user_devices.KU060AD9144SignalGenerator.labscript_devices import KU060AD9144SignalGenerator

# KU060AD9144SignalGenerator(
#     name='KU', com_port='COM5', baud_rate=115200, trigger_device=intermediate_device, trigger_connection='1'
# ) 

from user_devices.ARTIQ.labscript_devices import ARTIQ_Master, ARTIQ_DDS

ARTIQ_Master(
    name='ARTIQ',
    artiq_ip='127.0.0.1', # 改为 127.0.0.1 用于本地测试
    broadcast_port=5555,
    feedback_port=5556
)

DigitalOut(name='ttl0', parent_device=ARTIQ.outputs, connection='ttl0')
DigitalOut(name='ttl1', parent_device=ARTIQ.outputs, connection='ttl1')

AnalogOut(name='dac0', parent_device=ARTIQ.outputs, connection='fastino0_channel0')
AnalogOut(name='dac1', parent_device=ARTIQ.outputs, connection='fastino0_channel1')

urukul0 = ARTIQ_DDS(name='urukul0_ch0', parent_device=ARTIQ.outputs, connection='urukul0_ch0')


if __name__ == '__main__':
    # Begin issuing labscript primitives
    # start() elicits the commencement of the shot
    start()

    # Stop the experiment shot with stop()
    stop(1.0)
