#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <leptonica/allheaders.h>
#include <tesseract/capi.h>

static void usage(const char *prog) {
    fprintf(stderr, "uso: %s [-l idioma] [-d tessdata] imagem [imagem ...]\n", prog);
}

int main(int argc, char **argv) {
    const char *lang = "por";
    const char *datapath = NULL;
    int first = 1;

    while (first < argc && argv[first][0] == '-') {
        if (strcmp(argv[first], "-l") == 0 && first + 1 < argc) {
            lang = argv[first + 1];
            first += 2;
        } else if (strcmp(argv[first], "-d") == 0 && first + 1 < argc) {
            datapath = argv[first + 1];
            first += 2;
        } else {
            usage(argv[0]);
            return 1;
        }
    }
    if (first >= argc) {
        usage(argv[0]);
        return 1;
    }

    TessBaseAPI *api = TessBaseAPICreate();
    if (TessBaseAPIInit3(api, datapath, lang) != 0) {
        fprintf(stderr, "ocr: não foi possível iniciar o Tesseract com o idioma '%s'\n", lang);
        TessBaseAPIDelete(api);
        return 2;
    }
    TessBaseAPISetPageSegMode(api, PSM_AUTO);

    int failures = 0;
    for (int i = first; i < argc; i++) {
        PIX *image = pixRead(argv[i]);
        if (image == NULL) {
            fprintf(stderr, "ocr: não foi possível ler %s\n", argv[i]);
            failures++;
            continue;
        }
        TessBaseAPISetImage2(api, image);
        char *text = TessBaseAPIGetUTF8Text(api);
        if (text != NULL) {
            fputs(text, stdout);
            TessDeleteText(text);
        } else {
            fprintf(stderr, "ocr: falha no reconhecimento de %s\n", argv[i]);
            failures++;
        }
        if (i + 1 < argc)
            fputc('\f', stdout);
        pixDestroy(&image);
    }

    TessBaseAPIEnd(api);
    TessBaseAPIDelete(api);
    /* 3: alguma imagem falhou, mas o texto das demais saiu no stdout */
    return failures ? 3 : 0;
}
