package workers

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	ufmtv1 "github.com/gustavoz65/Chrono-BOT/ai/gen/ufmtv1"
)

func runner(t *testing.T) *Exec {
	t.Helper()
	root, err := FindRoot()
	if err != nil {
		t.Fatal(err)
	}
	return NewExec(root)
}

func TestPythonWorkerReal(t *testing.T) {
	e := runner(t)
	if err := exec.Command(e.Python, "-c", "import google.protobuf, bs4").Run(); err != nil {
		t.Skip("python com as dependências do projeto não encontrado (defina PYTHON)")
	}
	resp, err := e.Call(context.Background(), Python, &ufmtv1.Request{Payload: &ufmtv1.Request_Extract{
		Extract: &ufmtv1.ExtractRequest{Text: "Inscrições de 10/11/2026 a 16/11/2026", Today: "2026-10-23"}}})
	if err != nil {
		t.Fatal(err)
	}
	if got := resp.GetExtract().GetStatus(); got != "prevista" {
		t.Errorf("status = %q", got)
	}
}

func TestOCRWorkerReal(t *testing.T) {
	e := runner(t)
	if _, err := os.Stat(filepath.Join(e.Root, "ocr", "bin", "ufmt-ocr")); err != nil {
		t.Skip("binário do OCR ausente (make -C ocr)")
	}
	resp, err := e.Call(context.Background(), OCR, &ufmtv1.Request{Payload: &ufmtv1.Request_Ocr{Ocr: &ufmtv1.OcrRequest{
		Source: &ufmtv1.OcrRequest_Path{Path: filepath.Join(e.Root, "tests", "fixtures", "edital_escaneado.pdf")}}}})
	if err != nil {
		t.Fatal(err)
	}
	if pages := resp.GetOcr().GetPages(); len(pages) != 1 || !strings.Contains(pages[0].Text, "16/11/2026") {
		t.Errorf("páginas = %v", pages)
	}
}

func TestErroDoWorkerVemTipado(t *testing.T) {
	e := runner(t)
	if _, err := os.Stat(filepath.Join(e.Root, "ocr", "bin", "ufmt-ocr")); err != nil {
		t.Skip("binário do OCR ausente (make -C ocr)")
	}
	_, err := e.Call(context.Background(), OCR, &ufmtv1.Request{Payload: &ufmtv1.Request_Render{Render: &ufmtv1.RenderRequest{}}})
	var werr *Error
	if !errors.As(err, &werr) || werr.Code != ufmtv1.Error_CODE_UNSUPPORTED || werr.Worker != OCR {
		t.Errorf("erro = %#v", err)
	}
}

func TestExecutavelAusente(t *testing.T) {
	e := runner(t)
	e.Python = "/nao/existe/python"
	_, err := e.Call(context.Background(), Python, &ufmtv1.Request{})
	var werr *Error
	if !errors.As(err, &werr) || werr.Code != ufmtv1.Error_CODE_UNAVAILABLE {
		t.Errorf("erro = %#v", err)
	}
}
