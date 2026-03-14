# MaterialSelector for Klipper
# by Julian Burton (robotfishe) 2026
# GNU GPL v3 licence

import logging

class MaterialSelector:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.name = config.get_name().split()[-1]
        self.mmu_type = config.get('mmu_type', 'afc').lower()

        self.buttons = self.printer.load_object(config, 'buttons')
        self.gcode = self.printer.lookup_object('gcode')

        self.is_ready = False
        self.printer.register_event_handler("klippy:ready", self._handle_ready)

        self.num_presets = config.getfloat('num_presets', 3, above=1)
        self.presets = {}        # pin_name -> material
        self.button_states = {}  # pin_name -> current state (0 or 1)
        self.current_material = None

        # time switch must remain on a given setting before command is sent - default 0.5 seconds
        self.settle_time = config.getfloat('settle_time', 0.5, above=0.0)
        self.settle_timer_handler = None

        # presets
        for i in range(1, self.num_presets + 1):
            pin_name = config.get(f'preset{i}_pin')
            mat_name = config.get(f'preset{i}_material')
            if pin_name and mat_name:
                self.presets[pin_name] = mat_name
                self.button_states[pin_name] = 0
                self.buttons.register_debounce_button(pin_name,
                    lambda et, s, p=pin_name: self._button_handler(et, s, p), config)
            elif pin_name or mat_name:
                raise config.error(f"Preset {i} is missing either a pin or a material name!")

        # custom pin
        self.custom_pin = config.get('custom_pin')
        if self.custom_pin:
            self.button_states[self.custom_pin] = 0
            self.buttons.register_debounce_button(self.custom_pin,
                lambda et, s, p=self.custom_pin: self._button_handler(et, s, p), config)

        self.gcode.register_mux_command("RESET_LANE_MATERIAL", "LANE", self.name,
                                        self.cmd_RESET_LANE_MATERIAL,
                                        desc="Resync MMU material after lane reload")

        logging.info(f"MaterialSelector [{self.name}]: Initialized")

    def _button_handler(self, eventtime, state, pin_name):
        self.button_states[pin_name] = state

        # If a timer is pending, we don't need to unregister it
        # (Klipper timers are efficient), we just let the next one
        # overwrite the handler reference.
        # However, to be clean:
        if self.settle_timer_handler is not None:
            self.reactor.update_timer(self.settle_timer_handler, self.reactor.NEVER)

        # Fire _check_and_dispatch once the timer completes
        self.settle_timer_handler = self.reactor.register_timer(
            self._check_and_dispatch, self.reactor.monotonic() + self.settle_time)

    def _handle_ready(self):
        self.is_ready = True
        self.cmd_RESET_LANE_MATERIAL(None)

    def _check_and_dispatch(self, eventtime):
        # check pin states for current switch position; default to assuming switch is moving between states
        detected = "TRANSITION"

        # detect if switch is on a preset pin
        for pin, mat in self.presets.items():
            if self.button_states.get(pin, 0):
                detected = mat
                break

        # detect if switch is on custom pin
        if detected == "TRANSITION" and self.custom_pin and self.button_states.get(self.custom_pin, 0):
            detected = "CUSTOM"

        # if switch position has changed since _check_and_dispatch was last fired, send new detected state
        if detected != "TRANSITION" and (detected != self.current_material or self.current_material is None):
            logging.info(f"MaterialSelector [{self.name}]: Stable state: {detected}")
            self.current_material = detected
            # We schedule this for the NEXT reactor cycle to escape the timer context
            self.reactor.register_callback(lambda e: self._dispatch_mmu_cmd())

        self.settle_timer_handler = None
        return self.reactor.NEVER

    def _dispatch_mmu_cmd(self):
        if not self.is_ready:
            return

        mat_to_send = "Unknown" if self.current_material == "CUSTOM" else self.current_material
        mmu_cmd = ""

        if self.mmu_type == 'happy_hare':
            mmu_cmd = f"MMU_GATE_MAP GATE={self.name} MATERIAL={mat_to_send}"
        elif self.mmu_type == 'afc':
            mmu_cmd = f"SET_MATERIAL LANE={self.name} MATERIAL={mat_to_send}"

        if mmu_cmd:
            try:
                self.gcode.run_script_from_command(mmu_cmd)
            except Exception as e:
                logging.error(f"MaterialSelector: Error running command: {str(e)}")

    def cmd_RESET_LANE_MATERIAL(self, gcmd):
        # this macro should be called by the MMU when a new spool is loaded. it will re-apply the selector's current value.

        self.current_material = None

        dummy_pin = self.custom_pin if self.custom_pin else list(self.presets.keys())[0]
        self._button_handler(0, self.button_states.get(dummy_pin, 0), dummy_pin)

def load_config_prefix(config):
    return MaterialSelector(config)
