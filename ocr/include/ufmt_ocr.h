#ifndef UFMT_OCR_H
#define UFMT_OCR_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    UFMT_OCR_OK = 0,
    UFMT_OCR_EINVAL,
    UFMT_OCR_EINIT,
    UFMT_OCR_EOPEN,
    UFMT_OCR_ERENDER,
    UFMT_OCR_ERECOGNIZE,
    UFMT_OCR_ENOMEM
} ufmt_ocr_status;

typedef struct ufmt_ocr ufmt_ocr;

typedef struct {
    int number;
    char *text;
    int confidence;
} ufmt_ocr_page;

typedef struct {
    ufmt_ocr_page *pages;
    size_t count;
} ufmt_ocr_result;

typedef struct {
    const char *lang;
    const char *datapath;
    int dpi;
    int first_page;
    int last_page; /* 0 = até a última */
} ufmt_ocr_options;

#define UFMT_OCR_OPTIONS_DEFAULT {"por", NULL, 200, 1, 0}

ufmt_ocr_status ufmt_ocr_open(ufmt_ocr **out, const ufmt_ocr_options *opts);
void ufmt_ocr_close(ufmt_ocr *ocr);

ufmt_ocr_status ufmt_ocr_pdf_data(ufmt_ocr *ocr, const unsigned char *data, size_t len, ufmt_ocr_result *out);
ufmt_ocr_status ufmt_ocr_pdf_file(ufmt_ocr *ocr, const char *path, ufmt_ocr_result *out);
ufmt_ocr_status ufmt_ocr_image_file(ufmt_ocr *ocr, const char *path, ufmt_ocr_result *out);

void ufmt_ocr_result_free(ufmt_ocr_result *result);

const char *ufmt_ocr_strerror(ufmt_ocr_status status);
const char *ufmt_ocr_last_error(const ufmt_ocr *ocr);

#ifdef __cplusplus
}
#endif

#endif
