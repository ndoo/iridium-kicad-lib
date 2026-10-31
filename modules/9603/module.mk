# The 9603 models are generated from module.json. The Wuerth footprint variant copies the standoff pads
# from the KiCad footprint fetched from assets.lock, and the models include the KiCad U.FL STEP fetched from
# assets.lock; each footprint's model adds its own standoffs.
M9603        := modules/9603
WUERTH_9603  := $(THIRD_PARTY)/kicad/Mounting_Wuerth_WA-SMSI-M2_H6mm_9774060243.kicad_mod
UFL_9603     := $(THIRD_PARTY)/kicad/U.FL_Hirose_U.FL-R-SMT-1_Vertical.step
FP_9603_WA   := $(OUT)/$(LIB).pretty/Iridium_9603_WA-SMSI-M2.kicad_mod
STEP_9603    := $(OUT)/$(LIB).3dshapes/Iridium_9603.step
STEP_9603_WA := $(OUT)/$(LIB).3dshapes/Iridium_9603_WA-SMSI-M2.step

FOOTPRINTS += $(FP_9603_WA)
STEPS      += $(STEP_9603_WA)

$(WUERTH_9603): assets.lock tools/fetch.py
	$(PYTHON) tools/fetch.py --dest $(THIRD_PARTY) kicad-wuerth-wa-smsi-m2-h6-footprint
	@touch $@

$(UFL_9603): assets.lock tools/fetch.py
	$(PYTHON) tools/fetch.py --dest $(THIRD_PARTY) kicad-ufl-step
	@touch $@

$(OUT)/$(LIB).pretty/Iridium_9603.kicad_mod: $(M9603)/pads.json

$(FP_9603_WA): tools/kicad.py $(M9603)/module.json $(M9603)/pads.json $(WUERTH_9603)
	$(PYTHON) tools/kicad.py footprint --module $(M9603) --variant WA-SMSI-M2 --out $@

$(STEP_9603): tools/model.py $(M9603)/module.json $(UFL_9603)
	@mkdir -p $(dir $@)
	$(PYTHON) tools/model.py parametric --module $(M9603) --out $@

# The variant's footprint loads a model named after itself; it adds the Wuerth standoffs
$(STEP_9603_WA): tools/model.py $(M9603)/module.json $(UFL_9603)
	@mkdir -p $(dir $@)
	$(PYTHON) tools/model.py parametric --module $(M9603) --variant WA-SMSI-M2 --out $@
