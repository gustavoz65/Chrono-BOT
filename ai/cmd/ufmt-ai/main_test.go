package main

import (
	"context"
	"testing"

	"google.golang.org/protobuf/proto"

	ufmtv1 "github.com/gustavoz65/Chrono-BOT/ai/gen/ufmtv1"
)

func call(t *testing.T, input []byte) *ufmtv1.Response {
	t.Helper()
	return handle(context.Background(), input)
}

func TestWorkerRecusaEntradasInvalidas(t *testing.T) {
	if got := call(t, []byte{0xff, 0xff}).GetError().GetCode(); got != ufmtv1.Error_CODE_INVALID_REQUEST {
		t.Errorf("protobuf inválido: código %v", got)
	}
	ocr, _ := proto.Marshal(&ufmtv1.Request{Payload: &ufmtv1.Request_Ocr{Ocr: &ufmtv1.OcrRequest{}}})
	if got := call(t, ocr).GetError().GetCode(); got != ufmtv1.Error_CODE_UNSUPPORTED {
		t.Errorf("payload de outro worker: código %v", got)
	}
}

func TestSemChaveDoGroqRespondeIndisponivel(t *testing.T) {
	t.Setenv("GROQ_API_KEY", "")
	req, _ := proto.Marshal(&ufmtv1.Request{Payload: &ufmtv1.Request_Analyze{Analyze: &ufmtv1.AnalyzeRequest{Body: "x"}}})
	if got := call(t, req).GetError().GetCode(); got != ufmtv1.Error_CODE_UNAVAILABLE {
		t.Errorf("código %v", got)
	}
}
