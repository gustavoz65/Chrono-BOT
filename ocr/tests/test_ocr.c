#define _GNU_SOURCE

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "ufmt/v1/worker.pb-c.h"
#include "ufmt_ocr.h"
#include "worker.h"

static int failures;

#define CHECK(cond)                                                          \
    do {                                                                     \
        if (!(cond)) {                                                       \
            fprintf(stderr, "%s:%d: falhou: %s\n", __FILE__, __LINE__, #cond); \
            failures++;                                                      \
        }                                                                    \
    } while (0)

static const char *fixture;

static unsigned char *slurp(const char *path, size_t *len) {
    FILE *f = fopen(path, "rb");
    if (!f)
        return NULL;
    fseek(f, 0, SEEK_END);
    *len = (size_t)ftell(f);
    rewind(f);
    unsigned char *buf = malloc(*len);
    if (buf && fread(buf, 1, *len, f) != *len) {
        free(buf);
        buf = NULL;
    }
    fclose(f);
    return buf;
}

static void test_pdf_escaneado(void) {
    ufmt_ocr *ocr = NULL;
    CHECK(ufmt_ocr_open(&ocr, NULL) == UFMT_OCR_OK);
    ufmt_ocr_result r = {0};
    CHECK(ufmt_ocr_pdf_file(ocr, fixture, &r) == UFMT_OCR_OK);
    CHECK(r.count == 1);
    if (r.count) {
        CHECK(r.pages[0].number == 1);
        CHECK(strstr(r.pages[0].text, "10/11/2026 a 16/11/2026") != NULL);
        CHECK(strcasestr(r.pages[0].text, "computação") != NULL);
        CHECK(r.pages[0].confidence > 60);
    }
    ufmt_ocr_result_free(&r);
    CHECK(r.pages == NULL && r.count == 0);
    ufmt_ocr_close(ocr);
}

static void test_erros(void) {
    ufmt_ocr *ocr = NULL;
    ufmt_ocr_options bad = UFMT_OCR_OPTIONS_DEFAULT;
    bad.lang = "idioma_inexistente";
    CHECK(ufmt_ocr_open(&ocr, &bad) == UFMT_OCR_EINIT && ocr == NULL);

    CHECK(ufmt_ocr_open(&ocr, NULL) == UFMT_OCR_OK);
    ufmt_ocr_result r = {0};
    CHECK(ufmt_ocr_pdf_file(ocr, "/nao/existe.pdf", &r) == UFMT_OCR_EOPEN);
    CHECK(strstr(ufmt_ocr_last_error(ocr), "/nao/existe.pdf") != NULL);
    const unsigned char lixo[] = "isto não é um PDF";
    CHECK(ufmt_ocr_pdf_data(ocr, lixo, sizeof lixo, &r) == UFMT_OCR_EOPEN);
    CHECK(ufmt_ocr_pdf_data(ocr, NULL, 0, &r) == UFMT_OCR_EINVAL);
    ufmt_ocr_close(ocr);
    ufmt_ocr_close(NULL);
    CHECK(strcmp(ufmt_ocr_strerror(UFMT_OCR_OK), "ok") == 0);
}

static Ufmt__V1__Response *call_worker(const Ufmt__V1__Request *req) {
    size_t in_len = ufmt__v1__request__get_packed_size(req);
    uint8_t *in = malloc(in_len ? in_len : 1);
    ufmt__v1__request__pack(req, in);
    uint8_t *out = NULL;
    size_t out_len = 0;
    CHECK(ufmt_ocr_worker_handle(in, in_len, &out, &out_len) == 0);
    Ufmt__V1__Response *resp = ufmt__v1__response__unpack(NULL, out_len, out);
    free(in);
    free(out);
    return resp;
}

static void test_worker_protobuf(void) {
    size_t len = 0;
    unsigned char *pdf = slurp(fixture, &len);
    CHECK(pdf != NULL);

    Ufmt__V1__OcrRequest ocr_req;
    ufmt__v1__ocr_request__init(&ocr_req);
    ocr_req.source_case = UFMT__V1__OCR_REQUEST__SOURCE_PDF;
    ocr_req.pdf.data = pdf;
    ocr_req.pdf.len = len;
    Ufmt__V1__Request req;
    ufmt__v1__request__init(&req);
    req.payload_case = UFMT__V1__REQUEST__PAYLOAD_OCR;
    req.ocr = &ocr_req;

    Ufmt__V1__Response *resp = call_worker(&req);
    CHECK(resp && resp->payload_case == UFMT__V1__RESPONSE__PAYLOAD_OCR && !resp->error);
    if (resp && resp->ocr) {
        CHECK(resp->ocr->n_pages == 1);
        CHECK(resp->ocr->n_pages && strstr(resp->ocr->pages[0]->text, "ADMISS") != NULL);
    }
    ufmt__v1__response__free_unpacked(resp, NULL);
    free(pdf);

    Ufmt__V1__Request vazio;
    ufmt__v1__request__init(&vazio);
    resp = call_worker(&vazio);
    CHECK(resp && resp->error && resp->error->code == UFMT__V1__ERROR__CODE__CODE_UNSUPPORTED);
    ufmt__v1__response__free_unpacked(resp, NULL);

    uint8_t *out = NULL;
    size_t out_len = 0;
    const uint8_t lixo[] = {0xff, 0xff, 0xff};
    CHECK(ufmt_ocr_worker_handle(lixo, sizeof lixo, &out, &out_len) == 0);
    resp = ufmt__v1__response__unpack(NULL, out_len, out);
    CHECK(resp && resp->error && resp->error->code == UFMT__V1__ERROR__CODE__CODE_INVALID_REQUEST);
    ufmt__v1__response__free_unpacked(resp, NULL);
    free(out);
}

int main(int argc, char **argv) {
    fixture = argc > 1 ? argv[1] : "../tests/fixtures/edital_escaneado.pdf";
    test_pdf_escaneado();
    test_erros();
    test_worker_protobuf();
    if (failures) {
        fprintf(stderr, "%d verificação(ões) falharam\n", failures);
        return 1;
    }
    puts("ok: todos os testes do OCR passaram");
    return 0;
}
