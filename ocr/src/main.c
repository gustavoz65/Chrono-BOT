#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

#include "ufmt_ocr.h"
#include "worker.h"

static void usage(FILE *f) {
    fputs("uso: ufmt-ocr --worker            (Request protobuf no stdin, Response no stdout)\n"
          "     ufmt-ocr [opções] arquivo     (PDF ou imagem; texto no stdout)\n"
          "opções: -l idioma (por)  -d tessdata  -r dpi (200)  -f primeira  -n última\n",
          f);
}

static int read_all(FILE *f, uint8_t **buf, size_t *len) {
    size_t cap = 1 << 16;
    *len = 0;
    *buf = malloc(cap);
    if (!*buf)
        return -1;
    for (;;) {
        if (*len == cap) {
            uint8_t *bigger = realloc(*buf, cap *= 2);
            if (!bigger)
                return -1;
            *buf = bigger;
        }
        size_t n = fread(*buf + *len, 1, cap - *len, f);
        *len += n;
        if (n == 0)
            return ferror(f) ? -1 : 0;
    }
}

static int worker_mode(void) {
    uint8_t *in = NULL, *out = NULL;
    size_t in_len = 0, out_len = 0;
    if (read_all(stdin, &in, &in_len) != 0) {
        fprintf(stderr, "ufmt-ocr: falha ao ler stdin: %s\n", strerror(errno));
        free(in);
        return 1;
    }
    int rc = ufmt_ocr_worker_handle(in, in_len, &out, &out_len);
    free(in);
    if (rc != 0) {
        fputs("ufmt-ocr: memória insuficiente\n", stderr);
        return 1;
    }
    size_t written = fwrite(out, 1, out_len, stdout);
    free(out);
    return written == out_len && fflush(stdout) == 0 ? 0 : 1;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--worker") == 0)
        return worker_mode();
    if (argc == 2 && (strcmp(argv[1], "-h") == 0 || strcmp(argv[1], "--help") == 0)) {
        usage(stdout);
        return 0;
    }

    ufmt_ocr_options opts = UFMT_OCR_OPTIONS_DEFAULT;
    int c;
    while ((c = getopt(argc, argv, "l:d:r:f:n:")) != -1) {
        switch (c) {
        case 'l': opts.lang = optarg; break;
        case 'd': opts.datapath = optarg; break;
        case 'r': opts.dpi = atoi(optarg); break;
        case 'f': opts.first_page = atoi(optarg); break;
        case 'n': opts.last_page = atoi(optarg); break;
        default: usage(stderr); return 2;
        }
    }
    if (optind != argc - 1) {
        usage(stderr);
        return 2;
    }

    const char *path = argv[optind];
    ufmt_ocr *ocr = NULL;
    ufmt_ocr_status st = ufmt_ocr_open(&ocr, &opts);
    if (st != UFMT_OCR_OK) {
        fprintf(stderr, "ufmt-ocr: %s\n", ufmt_ocr_strerror(st));
        return 1;
    }
    ufmt_ocr_result result = {0};
    size_t n = strlen(path);
    st = n > 4 && strcasecmp(path + n - 4, ".pdf") == 0 ? ufmt_ocr_pdf_file(ocr, path, &result)
                                                         : ufmt_ocr_image_file(ocr, path, &result);
    if (st != UFMT_OCR_OK) {
        fprintf(stderr, "ufmt-ocr: %s: %s\n", ufmt_ocr_strerror(st), ufmt_ocr_last_error(ocr));
        ufmt_ocr_close(ocr);
        return 1;
    }
    for (size_t i = 0; i < result.count; i++)
        printf("--- página %d (confiança %d%%) ---\n%s", result.pages[i].number, result.pages[i].confidence,
               result.pages[i].text);
    ufmt_ocr_result_free(&result);
    ufmt_ocr_close(ocr);
    return 0;
}
