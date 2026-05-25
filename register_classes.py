#####################################################################
#                                                                   #
# /ARTIQ/register_classes.py                                        #
#                                                                   #
#####################################################################


from labscript_devices import register_classes

register_classes(
    'ARTIQ_Master',
    BLACS_tab='user_devices.ARTIQ.blacs_tabs.ARTIQ_MasterTab',
    # BLACS_worker='user_devices.ARTIQ.blacs_worker.ARTIQ_Worker',
)
