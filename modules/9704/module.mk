# 3D model build for the 9704: a vendor-derived shielding can plus a generated label.
M9704            := modules/9704
LABEL_9704       := $(OUT)/build/9704/label.svg
CAN_9704         := $(OUT)/build/9704/can.step
VENDOR_STEP_9704 := $(THIRD_PARTY)/vendor/RockBLOCK_9704-SMA-2A.step

LABELS += $(LABEL_9704)
CANS   += $(CAN_9704)

$(LABEL_9704): $(FONTS_STAMP) $(M9704)/label.py
	@mkdir -p $(dir $@)
	$(PYTHON) $(M9704)/label.py --fonts $(THIRD_PARTY)/fonts --assets $(THIRD_PARTY)/logos --out $@

$(CAN_9704): $(FONTS_STAMP) tools/model.py $(M9704)/module.json
	@mkdir -p $(dir $@)
	$(PYTHON) tools/model.py can --module $(M9704) --vendor $(VENDOR_STEP_9704) --out $@

$(OUT)/$(LIB).3dshapes/Iridium_9704.step: $(LABEL_9704) $(CAN_9704) tools/model.py $(M9704)/module.json
	@mkdir -p $(dir $@)
	$(PYTHON) tools/model.py assemble --module $(M9704) --label $(LABEL_9704) --can $(CAN_9704) --out $@
