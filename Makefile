# countdown-leader — batch driver.
#
# Usage:
#   make             # build all 90 files (smpte + simple heads, tail)
#   make -j8         # parallel
#   make smoke       # one representative file
#   make clean       # wipe out/

.DEFAULT_GOAL := all

PY := .venv/bin/python
GEN := generate.py

OUT := out

STYLES := smpte simple tail
FPSES := 2397 24 25

# Resolution variants: name -> "WIDTH HEIGHT [active_aspect]"
#   hd        1920x1080         16:9
#   hd-scope  1920x1080 + 2.39  letterboxed scope (active 1920x804)
#   uhd       3840x2160         16:9
#   uhd-scope 3840x2160 + 2.39  letterboxed scope (active 3840x1608)
#   2k-full   2048x1080         full container
#   2k-flat   1998x1080         DCP Flat
#   4k-full   4096x2160         full container
#   4k-flat   3996x2160         DCP Flat
#   2k-scope  2048x858          native 2.39 DCI scope
#   4k-scope  4096x1716         native 2.39 DCI scope
RESES := hd hd-scope uhd uhd-scope 2k-full 4k-full 2k-flat 4k-flat 2k-scope 4k-scope

# Per-res spec lookups.
res_w_hd        := 1920
res_h_hd        := 1080
res_aspect_hd   :=
res_w_hd-scope  := 1920
res_h_hd-scope  := 1080
res_aspect_hd-scope := --active-aspect 2.39
res_w_uhd       := 3840
res_h_uhd       := 2160
res_aspect_uhd  :=
res_w_uhd-scope := 3840
res_h_uhd-scope := 2160
res_aspect_uhd-scope := --active-aspect 2.39
res_w_2k-full   := 2048
res_h_2k-full   := 1080
res_aspect_2k-full :=
res_w_2k-flat   := 1998
res_h_2k-flat   := 1080
res_aspect_2k-flat :=
res_w_4k-full   := 4096
res_h_4k-full   := 2160
res_aspect_4k-full :=
res_w_4k-flat   := 3996
res_h_4k-flat   := 2160
res_aspect_4k-flat :=
res_w_2k-scope  := 2048
res_h_2k-scope  := 858
res_aspect_2k-scope :=
res_w_4k-scope  := 4096
res_h_4k-scope  := 1716
res_aspect_4k-scope :=

# Map fps label -> numeric fps for the script.
fps_num_2397 := 23.976
fps_num_24   := 24
fps_num_25   := 25

# Build the list of all target files.
ALL :=
define gen_target
ALL += $(OUT)/$(1)/$(1)_$(2)_$(3).mov
$(OUT)/$(1)/$(1)_$(2)_$(3).mov: $(GEN) media_spec.py verify_media.py Makefile fonts/BigShouldersDisplay.ttf fonts/Inter-Medium.ttf
	@mkdir -p $$(dir $$@)
	$(PY) $(GEN) --style $(1) --fps $(fps_num_$(3)) \
		--width $(res_w_$(2)) --height $(res_h_$(2)) \
		$(res_aspect_$(2)) --out $$@
endef

$(foreach s,$(STYLES),\
  $(foreach r,$(RESES),\
    $(foreach f,$(FPSES),\
      $(eval $(call gen_target,$(s),$(r),$(f))))))

.PHONY: all smoke verify package test clean list
all: $(ALL)

smoke: $(OUT)/simple/simple_hd_24.mov
	$(PY) verify_media.py $<

verify: all
	$(PY) verify_media.py --root $(OUT) --report /tmp/countdown-leader-qa.json

package: all
	./package.sh

test:
	$(PY) -m unittest discover -s tests -v

list:
	@printf '%s\n' $(ALL)

clean:
	rm -rf $(OUT)
