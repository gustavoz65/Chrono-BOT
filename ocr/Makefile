# Dependências (Debian/Ubuntu): libtesseract-dev libleptonica-dev tesseract-ocr-por
#   libpoppler-glib-dev libcairo2-dev libprotobuf-c-dev pkg-config
CC      ?= cc
PKGS     = tesseract lept poppler-glib cairo libprotobuf-c
CFLAGS  ?= -O2 -g
WARN     = -std=c11 -Wall -Wextra -Wpedantic -Werror
CPPFLAGS = -Iinclude -Igen $(shell pkg-config --cflags $(PKGS))
LDLIBS   = $(shell pkg-config --libs $(PKGS))

LIB_OBJ  = build/ocr.o build/worker.o build/worker.pb-c.o
FIXTURE  = ../tests/fixtures/edital_escaneado.pdf

.PHONY: all test asan clean

all: bin/ufmt-ocr

build/%.o: src/%.c include/ufmt_ocr.h src/worker.h | build
	$(CC) $(CPPFLAGS) $(WARN) $(CFLAGS) -c -o $@ $<

build/worker.pb-c.o: gen/ufmt/v1/worker.pb-c.c | build
	$(CC) $(CPPFLAGS) -std=c11 $(CFLAGS) -c -o $@ $<

build/libufmtocr.a: $(LIB_OBJ)
	$(AR) rcs $@ $^

bin/ufmt-ocr: build/main.o build/libufmtocr.a | bin
	$(CC) $(CFLAGS) -o $@ $^ $(LDLIBS)

bin/test_ocr: tests/test_ocr.c build/libufmtocr.a | bin
	$(CC) $(CPPFLAGS) -Isrc $(WARN) $(CFLAGS) -o $@ $^ $(LDLIBS)

test: bin/test_ocr bin/ufmt-ocr
	./bin/test_ocr $(FIXTURE)

asan:
	$(MAKE) clean
	$(MAKE) test CFLAGS="-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer"

build bin:
	mkdir -p $@

clean:
	rm -rf build bin
