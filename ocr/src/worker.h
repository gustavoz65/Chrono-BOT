#ifndef UFMT_OCR_WORKER_H
#define UFMT_OCR_WORKER_H

#include <stddef.h>
#include <stdint.h>

/* Processa um ufmt.v1.Request serializado e devolve um ufmt.v1.Response
 * serializado em *out (alocado com malloc). Retorna 0, ou -1 sem memória. */
int ufmt_ocr_worker_handle(const uint8_t *in, size_t in_len, uint8_t **out, size_t *out_len);

#endif
