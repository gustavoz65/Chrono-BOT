#define _POSIX_C_SOURCE 200809L

#include "ufmt_ocr.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <cairo.h>
#include <leptonica/allheaders.h>
#include <poppler.h>
#include <tesseract/capi.h>

struct ufmt_ocr {
    TessBaseAPI *api;
    ufmt_ocr_options opts;
    char error[256];
};

static ufmt_ocr_status fail(ufmt_ocr *ocr, ufmt_ocr_status status, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(ocr->error, sizeof ocr->error, fmt, ap);
    va_end(ap);
    return status;
}

const char *ufmt_ocr_strerror(ufmt_ocr_status status) {
    switch (status) {
    case UFMT_OCR_OK: return "ok";
    case UFMT_OCR_EINVAL: return "argumento inválido";
    case UFMT_OCR_EINIT: return "falha ao iniciar o Tesseract";
    case UFMT_OCR_EOPEN: return "não foi possível abrir o arquivo";
    case UFMT_OCR_ERENDER: return "falha ao renderizar a página do PDF";
    case UFMT_OCR_ERECOGNIZE: return "falha no reconhecimento de texto";
    case UFMT_OCR_ENOMEM: return "memória insuficiente";
    }
    return "erro desconhecido";
}

const char *ufmt_ocr_last_error(const ufmt_ocr *ocr) {
    return ocr ? ocr->error : "";
}

ufmt_ocr_status ufmt_ocr_open(ufmt_ocr **out, const ufmt_ocr_options *opts) {
    ufmt_ocr_options defaults = UFMT_OCR_OPTIONS_DEFAULT;
    if (!out)
        return UFMT_OCR_EINVAL;
    *out = NULL;
    ufmt_ocr *ocr = calloc(1, sizeof *ocr);
    if (!ocr)
        return UFMT_OCR_ENOMEM;
    ocr->opts = opts ? *opts : defaults;
    if (!ocr->opts.lang || !*ocr->opts.lang)
        ocr->opts.lang = defaults.lang;
    if (ocr->opts.dpi <= 0)
        ocr->opts.dpi = defaults.dpi;
    if (ocr->opts.first_page <= 0)
        ocr->opts.first_page = 1;

    ocr->api = TessBaseAPICreate();
    if (!ocr->api || TessBaseAPIInit3(ocr->api, ocr->opts.datapath, ocr->opts.lang) != 0) {
        if (ocr->api)
            TessBaseAPIDelete(ocr->api);
        free(ocr);
        return UFMT_OCR_EINIT;
    }
    TessBaseAPISetPageSegMode(ocr->api, PSM_AUTO);
    *out = ocr;
    return UFMT_OCR_OK;
}

void ufmt_ocr_close(ufmt_ocr *ocr) {
    if (!ocr)
        return;
    TessBaseAPIEnd(ocr->api);
    TessBaseAPIDelete(ocr->api);
    free(ocr);
}

void ufmt_ocr_result_free(ufmt_ocr_result *result) {
    if (!result)
        return;
    for (size_t i = 0; i < result->count; i++)
        free(result->pages[i].text);
    free(result->pages);
    result->pages = NULL;
    result->count = 0;
}

static ufmt_ocr_status recognize(ufmt_ocr *ocr, PIX *pix, int number, ufmt_ocr_page *page) {
    TessBaseAPISetImage2(ocr->api, pix);
    char *text = TessBaseAPIGetUTF8Text(ocr->api);
    if (!text)
        return fail(ocr, UFMT_OCR_ERECOGNIZE, "página %d: Tesseract não devolveu texto", number);
    page->text = strdup(text);
    TessDeleteText(text);
    if (!page->text)
        return UFMT_OCR_ENOMEM;
    page->number = number;
    page->confidence = TessBaseAPIMeanTextConf(ocr->api);
    return UFMT_OCR_OK;
}

/* Converte a superfície ARGB32 do cairo em uma imagem 8 bits em tons de cinza. */
static PIX *surface_to_pix(cairo_surface_t *surface) {
    int w = cairo_image_surface_get_width(surface);
    int h = cairo_image_surface_get_height(surface);
    int stride = cairo_image_surface_get_stride(surface);
    const unsigned char *src = cairo_image_surface_get_data(surface);
    PIX *pix = pixCreate(w, h, 8);
    if (!pix)
        return NULL;
    l_uint32 *data = pixGetData(pix);
    int wpl = pixGetWpl(pix);
    for (int y = 0; y < h; y++) {
        const uint32_t *row = (const uint32_t *)(src + (size_t)y * (size_t)stride);
        l_uint32 *line = data + (size_t)y * (size_t)wpl;
        for (int x = 0; x < w; x++) {
            uint32_t p = row[x];
            unsigned r = (p >> 16) & 0xff, g = (p >> 8) & 0xff, b = p & 0xff;
            SET_DATA_BYTE(line, x, (r * 299 + g * 587 + b * 114) / 1000);
        }
    }
    return pix;
}

static ufmt_ocr_status render_page(ufmt_ocr *ocr, PopplerPage *page, int number, PIX **out) {
    double wpt, hpt;
    poppler_page_get_size(page, &wpt, &hpt);
    double scale = ocr->opts.dpi / 72.0;
    int w = (int)(wpt * scale + 0.5), h = (int)(hpt * scale + 0.5);
    if (w <= 0 || h <= 0 || (long long)w * h > 60000000LL)
        return fail(ocr, UFMT_OCR_ERENDER, "página %d com tamanho inválido (%dx%d)", number, w, h);

    cairo_surface_t *surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, w, h);
    if (cairo_surface_status(surface) != CAIRO_STATUS_SUCCESS) {
        cairo_surface_destroy(surface);
        return fail(ocr, UFMT_OCR_ENOMEM, "página %d: sem memória para %dx%d", number, w, h);
    }
    cairo_t *cr = cairo_create(surface);
    cairo_set_source_rgb(cr, 1, 1, 1);
    cairo_paint(cr);
    cairo_scale(cr, scale, scale);
    poppler_page_render_for_printing(page, cr);
    cairo_destroy(cr);
    cairo_surface_flush(surface);

    *out = surface_to_pix(surface);
    cairo_surface_destroy(surface);
    if (*out)
        pixSetResolution(*out, ocr->opts.dpi, ocr->opts.dpi);
    return *out ? UFMT_OCR_OK : fail(ocr, UFMT_OCR_ENOMEM, "página %d: sem memória", number);
}

static ufmt_ocr_status ocr_document(ufmt_ocr *ocr, PopplerDocument *doc, ufmt_ocr_result *out) {
    int total = poppler_document_get_n_pages(doc);
    int first = ocr->opts.first_page;
    int last = ocr->opts.last_page > 0 && ocr->opts.last_page < total ? ocr->opts.last_page : total;
    if (first > last)
        return fail(ocr, UFMT_OCR_EINVAL, "intervalo de páginas %d-%d fora do documento (%d páginas)",
                    first, last, total);

    out->pages = calloc((size_t)(last - first + 1), sizeof *out->pages);
    if (!out->pages)
        return UFMT_OCR_ENOMEM;

    ufmt_ocr_status st = UFMT_OCR_OK;
    for (int n = first; n <= last && st == UFMT_OCR_OK; n++) {
        PopplerPage *page = poppler_document_get_page(doc, n - 1);
        if (!page) {
            st = fail(ocr, UFMT_OCR_ERENDER, "página %d não pôde ser aberta", n);
            break;
        }
        PIX *pix = NULL;
        st = render_page(ocr, page, n, &pix);
        g_object_unref(page);
        if (st == UFMT_OCR_OK)
            st = recognize(ocr, pix, n, &out->pages[out->count]);
        if (st == UFMT_OCR_OK)
            out->count++;
        pixDestroy(&pix);
    }
    if (st != UFMT_OCR_OK)
        ufmt_ocr_result_free(out);
    return st;
}

ufmt_ocr_status ufmt_ocr_pdf_data(ufmt_ocr *ocr, const unsigned char *data, size_t len, ufmt_ocr_result *out) {
    if (!ocr || !data || !len || !out)
        return UFMT_OCR_EINVAL;
    memset(out, 0, sizeof *out);
    GBytes *bytes = g_bytes_new(data, len);
    GError *err = NULL;
    PopplerDocument *doc = poppler_document_new_from_bytes(bytes, NULL, &err);
    g_bytes_unref(bytes);
    if (!doc) {
        ufmt_ocr_status st = fail(ocr, UFMT_OCR_EOPEN, "PDF inválido: %s", err ? err->message : "?");
        g_clear_error(&err);
        return st;
    }
    ufmt_ocr_status st = ocr_document(ocr, doc, out);
    g_object_unref(doc);
    return st;
}

ufmt_ocr_status ufmt_ocr_pdf_file(ufmt_ocr *ocr, const char *path, ufmt_ocr_result *out) {
    if (!ocr || !path || !out)
        return UFMT_OCR_EINVAL;
    gchar *contents = NULL;
    gsize len = 0;
    GError *err = NULL;
    if (!g_file_get_contents(path, &contents, &len, &err)) {
        ufmt_ocr_status st = fail(ocr, UFMT_OCR_EOPEN, "%s: %s", path, err ? err->message : "?");
        g_clear_error(&err);
        return st;
    }
    ufmt_ocr_status st = ufmt_ocr_pdf_data(ocr, (const unsigned char *)contents, len, out);
    g_free(contents);
    return st;
}

ufmt_ocr_status ufmt_ocr_image_file(ufmt_ocr *ocr, const char *path, ufmt_ocr_result *out) {
    if (!ocr || !path || !out)
        return UFMT_OCR_EINVAL;
    memset(out, 0, sizeof *out);
    PIX *pix = pixRead(path);
    if (!pix)
        return fail(ocr, UFMT_OCR_EOPEN, "%s: imagem ilegível", path);
    out->pages = calloc(1, sizeof *out->pages);
    ufmt_ocr_status st = out->pages ? recognize(ocr, pix, 1, &out->pages[0]) : UFMT_OCR_ENOMEM;
    pixDestroy(&pix);
    if (st == UFMT_OCR_OK)
        out->count = 1;
    else
        ufmt_ocr_result_free(out);
    return st;
}
