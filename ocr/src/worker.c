#include "worker.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ufmt/v1/worker.pb-c.h"
#include "ufmt_ocr.h"

static Ufmt__V1__Error__Code error_code(ufmt_ocr_status st) {
    switch (st) {
    case UFMT_OCR_EINVAL:
    case UFMT_OCR_EOPEN:
        return UFMT__V1__ERROR__CODE__CODE_INVALID_REQUEST;
    case UFMT_OCR_EINIT:
        return UFMT__V1__ERROR__CODE__CODE_UNAVAILABLE;
    default:
        return UFMT__V1__ERROR__CODE__CODE_INTERNAL;
    }
}

static int pack(const Ufmt__V1__Response *resp, uint8_t **out, size_t *out_len) {
    *out_len = ufmt__v1__response__get_packed_size(resp);
    *out = malloc(*out_len ? *out_len : 1);
    if (!*out)
        return -1;
    ufmt__v1__response__pack(resp, *out);
    return 0;
}

static int reply_error(Ufmt__V1__Error__Code code, const char *message, uint8_t **out, size_t *out_len) {
    Ufmt__V1__Response resp = UFMT__V1__RESPONSE__INIT;
    Ufmt__V1__Error err = UFMT__V1__ERROR__INIT;
    err.code = code;
    err.message = (char *)message;
    resp.error = &err;
    return pack(&resp, out, out_len);
}

static ufmt_ocr_status run(const Ufmt__V1__OcrRequest *req, ufmt_ocr **ocr, ufmt_ocr_result *result) {
    ufmt_ocr_options opts = UFMT_OCR_OPTIONS_DEFAULT;
    if (req->lang && *req->lang)
        opts.lang = req->lang;
    if (req->dpi > 0)
        opts.dpi = req->dpi;
    if (req->first_page > 0)
        opts.first_page = req->first_page;
    opts.last_page = req->last_page;

    ufmt_ocr_status st = ufmt_ocr_open(ocr, &opts);
    if (st != UFMT_OCR_OK)
        return st;
    switch (req->source_case) {
    case UFMT__V1__OCR_REQUEST__SOURCE_PDF:
        return ufmt_ocr_pdf_data(*ocr, req->pdf.data, req->pdf.len, result);
    case UFMT__V1__OCR_REQUEST__SOURCE_PATH: {
        size_t n = strlen(req->path);
        int is_pdf = n > 4 && (strcmp(req->path + n - 4, ".pdf") == 0 || strcmp(req->path + n - 4, ".PDF") == 0);
        return is_pdf ? ufmt_ocr_pdf_file(*ocr, req->path, result) : ufmt_ocr_image_file(*ocr, req->path, result);
    }
    default:
        return UFMT_OCR_EINVAL;
    }
}

int ufmt_ocr_worker_handle(const uint8_t *in, size_t in_len, uint8_t **out, size_t *out_len) {
    Ufmt__V1__Request *req = ufmt__v1__request__unpack(NULL, in_len, in);
    if (!req)
        return reply_error(UFMT__V1__ERROR__CODE__CODE_INVALID_REQUEST, "Request protobuf inválido", out, out_len);
    if (req->payload_case != UFMT__V1__REQUEST__PAYLOAD_OCR) {
        ufmt__v1__request__free_unpacked(req, NULL);
        return reply_error(UFMT__V1__ERROR__CODE__CODE_UNSUPPORTED, "este worker só atende OcrRequest", out, out_len);
    }

    ufmt_ocr *ocr = NULL;
    ufmt_ocr_result result = {0};
    ufmt_ocr_status st = run(req->ocr, &ocr, &result);
    int rc;
    if (st != UFMT_OCR_OK) {
        char message[320];
        const char *detail = ocr ? ufmt_ocr_last_error(ocr) : "";
        snprintf(message, sizeof message, "%s%s%s", ufmt_ocr_strerror(st), *detail ? ": " : "", detail);
        rc = reply_error(error_code(st), message, out, out_len);
    } else {
        Ufmt__V1__OcrPage *pages = calloc(result.count ? result.count : 1, sizeof *pages);
        Ufmt__V1__OcrPage **ptrs = calloc(result.count ? result.count : 1, sizeof *ptrs);
        if (!pages || !ptrs) {
            rc = -1;
        } else {
            for (size_t i = 0; i < result.count; i++) {
                ufmt__v1__ocr_page__init(&pages[i]);
                pages[i].number = result.pages[i].number;
                pages[i].text = result.pages[i].text;
                pages[i].confidence = result.pages[i].confidence;
                ptrs[i] = &pages[i];
            }
            Ufmt__V1__OcrResponse ocr_resp = UFMT__V1__OCR_RESPONSE__INIT;
            ocr_resp.n_pages = result.count;
            ocr_resp.pages = ptrs;
            Ufmt__V1__Response resp = UFMT__V1__RESPONSE__INIT;
            resp.payload_case = UFMT__V1__RESPONSE__PAYLOAD_OCR;
            resp.ocr = &ocr_resp;
            rc = pack(&resp, out, out_len);
        }
        free(pages);
        free(ptrs);
    }
    ufmt_ocr_result_free(&result);
    ufmt_ocr_close(ocr);
    ufmt__v1__request__free_unpacked(req, NULL);
    return rc;
}
