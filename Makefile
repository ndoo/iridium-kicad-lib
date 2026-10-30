PYTHON ?= python3
KICAD_CLI ?= kicad-cli
OUT    ?= out
THIRD_PARTY  ?= third_party

LIB       := Iridium_Modules
MODULES   ?= $(sort $(patsubst modules/%/module.json,%,$(wildcard modules/*/module.json)))
FOOTPRINTS := $(foreach m,$(MODULES),$(OUT)/$(LIB).pretty/Iridium_$(m).kicad_mod)
STEPS     := $(foreach m,$(MODULES),$(OUT)/$(LIB).3dshapes/Iridium_$(m).step)
SYMBOL    := $(OUT)/$(LIB).kicad_sym
ZIP       := $(OUT)/$(LIB)-kicad-lib.zip
INSTALL   := $(OUT)/build/INSTALL.txt
RENDER_DIR := $(OUT)/render
RENDERS   := $(foreach m,$(MODULES),$(RENDER_DIR)/$(m)/render-top.png)
MODULE_ARGS := $(addprefix --module modules/,$(MODULES))

FONTS_STAMP := $(THIRD_PARTY)/.fetched

# Each module.mk adds its label and can targets to these and defines the rule for its STEP
LABELS :=
CANS   :=
include $(foreach m,$(MODULES),modules/$(m)/module.mk)

.PHONY: all fetch verify footprint symbol label can step package render check klc clean distclean

all: footprint symbol step

fetch:
	THIRD_PARTY=$(THIRD_PARTY) $(PYTHON) tools/fetch.py --dest $(THIRD_PARTY)
	@touch $(FONTS_STAMP)

$(FONTS_STAMP): assets.lock tools/fetch.py
	THIRD_PARTY=$(THIRD_PARTY) $(PYTHON) tools/fetch.py --dest $(THIRD_PARTY)
	@touch $@

verify:
	$(PYTHON) tools/fetch.py --verify

footprint: $(FOOTPRINTS)
symbol: $(SYMBOL)
label: $(LABELS)
can: $(CANS)
step: $(STEPS)
package: $(ZIP)
render: $(RENDERS)

$(OUT)/$(LIB).pretty/Iridium_%.kicad_mod: tools/kicad.py modules/%/module.json modules/%/pads.json
	$(PYTHON) tools/kicad.py footprint --module modules/$* --out $@

$(SYMBOL): tools/kicad.py $(foreach m,$(MODULES),modules/$(m)/module.json)
	$(PYTHON) tools/kicad.py symbol $(MODULE_ARGS) --out $@

# Images of each module's footprint, symbol and 3D model; needs kicad-cli (KICAD_CLI) and rsvg-convert.
$(RENDER_DIR)/%/render-top.png: $(OUT)/$(LIB).pretty/Iridium_%.kicad_mod $(SYMBOL) $(OUT)/$(LIB).3dshapes/Iridium_%.step tools/render.py
	KICAD_CLI="$(KICAD_CLI)" $(PYTHON) tools/render.py --lib $(OUT) --out $(RENDER_DIR) --module modules/$*

$(INSTALL): Makefile
	@mkdir -p $(dir $@)
	@printf '%s\n' \
	  'Iridium Modules KiCad library' \
	  '' \
	  '1. Preferences > Manage Symbol Libraries: add $(LIB).kicad_sym with nickname $(LIB).' \
	  '2. Preferences > Manage Footprint Libraries: add $(LIB).pretty with nickname $(LIB).' \
	  '3. Preferences > Configure Paths: define IRIDIUM_MODULES_3DMODEL_DIR as the absolute path of the' \
	  '   directory this file is in. Each footprint loads' \
	  '   $${IRIDIUM_MODULES_3DMODEL_DIR}/$(LIB).3dshapes/<footprint name>.step' > $@

# Deterministic zip: fixed file order, timestamps normalised, no extra attributes.
$(ZIP): $(FOOTPRINTS) $(SYMBOL) $(STEPS) $(INSTALL)
	@rm -rf $(OUT)/build/stage $@
	@mkdir -p $(OUT)/build/stage
	@cp -R $(OUT)/$(LIB).pretty $(OUT)/$(LIB).3dshapes $(SYMBOL) $(INSTALL) $(OUT)/build/stage/
	@find $(OUT)/build/stage -exec touch -t 202001010000 {} +
	cd $(OUT)/build/stage && find . -type f | LC_ALL=C sort | sed 's|^\./||' | zip -X -q -@ $(abspath $@)
	@echo "wrote $@"

# CHECK_STEP=1 also requires every STEP model to exist and be non-empty.
CHECK_STEP ?= 0

check: $(FOOTPRINTS) $(SYMBOL)
	$(PYTHON) tools/check.py --lib $(OUT) $(if $(filter 1,$(CHECK_STEP)),--step) $(MODULE_ARGS)

# KLC_DIR is the library_utils directory of a kicad-library-tools checkout.
KLC_DIR ?= kicad-library-tools/library_utils

klc: $(FOOTPRINTS) $(SYMBOL)
	$(PYTHON) tools/klc_check.py --klc $(KLC_DIR) --lib $(OUT)

clean:
	rm -rf $(OUT)

distclean: clean
	rm -rf $(THIRD_PARTY)
