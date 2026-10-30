# The 9604 reuses the KiCad SARA footprint's pads and the KiCad SARA STEP, fetched from assets.lock.
M9604          := modules/9604
SARA_FP_9604   := $(THIRD_PARTY)/kicad/ublox_SARA_LGA-96.kicad_mod
SARA_STEP_9604 := $(THIRD_PARTY)/kicad/ublox_SARA_LGA-96.step

$(SARA_FP_9604): assets.lock tools/fetch.py
	$(PYTHON) tools/fetch.py --dest $(THIRD_PARTY) kicad-ublox-sara-footprint
	@touch $@

$(SARA_STEP_9604): assets.lock tools/fetch.py
	$(PYTHON) tools/fetch.py --dest $(THIRD_PARTY) kicad-ublox-sara-step
	@touch $@

$(OUT)/$(LIB).pretty/Iridium_9604.kicad_mod: $(SARA_FP_9604)

$(OUT)/$(LIB).3dshapes/Iridium_9604.step: $(SARA_STEP_9604) tools/model.py $(M9604)/module.json
	@mkdir -p $(dir $@)
	$(PYTHON) tools/model.py step-edit --module $(M9604) --out $@
