# Requer: libtesseract-dev libleptonica-dev tesseract-ocr-por (Debian/Ubuntu)
CC      ?= cc
CFLAGS  ?= -O2 -Wall -Wextra -std=c11
PKGS     = tesseract lept

bin/ocr: ocr.c
	mkdir -p bin
	$(CC) $(CFLAGS) $$(pkg-config --cflags $(PKGS)) -o $@ $< $$(pkg-config --libs $(PKGS))

clean:
	rm -rf bin

.PHONY: clean
