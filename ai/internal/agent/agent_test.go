package agent

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/firebase/genkit/go/ai"
	"github.com/firebase/genkit/go/genkit"

	ufmtv1 "github.com/gustavoz65/Chrono-BOT/ai/gen/ufmtv1"
	"github.com/gustavoz65/Chrono-BOT/ai/internal/workers"
)

var today = time.Date(2026, 10, 23, 12, 0, 0, 0, time.UTC)

const edital = "EDITAL Nº 09/2026 - PROEG/UFMT. ADMISSÃO DE GRADUADO 2026/2. " +
	"Período de inscrições: 10/11/2026 a 16/11/2026. Homologação das inscrições: 20/11/2026. " +
	"Ciência da Computação - Campus Cuiabá - 4 vagas."

type fakeRunner struct {
	mu    sync.Mutex
	calls []workers.Name
}

func (f *fakeRunner) Call(_ context.Context, w workers.Name, req *ufmtv1.Request) (*ufmtv1.Response, error) {
	f.mu.Lock()
	f.calls = append(f.calls, w)
	f.mu.Unlock()
	switch {
	case w == workers.Python && req.GetExtract() != nil:
		return &ufmtv1.Response{Payload: &ufmtv1.Response_Extract{Extract: &ufmtv1.ExtractResponse{
			Modalidade: "admissao_graduados", Status: "prevista", VagasCc: 4,
			Windows: []*ufmtv1.InscriptionWindow{{Start: "2026-11-10", End: "2026-11-16", Status: "prevista"}},
		}}}, nil
	case w == workers.OCR:
		return &ufmtv1.Response{Payload: &ufmtv1.Response_Ocr{Ocr: &ufmtv1.OcrResponse{
			Pages: []*ufmtv1.OcrPage{{Number: 1, Confidence: 93, Text: "cronograma escaneado"}}}}}, nil
	}
	return nil, &workers.Error{Worker: w, Code: ufmtv1.Error_CODE_UNSUPPORTED, Message: "não simulado"}
}

// scriptedModel imita o Groq: no 1º turno pede tools, no 2º lê as respostas
// delas e devolve o JSON final.
type scriptedModel struct {
	t           *testing.T
	firstTurn   []*ai.Part
	final       func(results map[string]any) Output
	offered     []string
	toolResults map[string]any
}

func (m *scriptedModel) generate(_ context.Context, req *ai.ModelRequest, _ ai.ModelStreamCallback) (*ai.ModelResponse, error) {
	m.offered = m.offered[:0]
	for _, td := range req.Tools {
		m.offered = append(m.offered, td.Name)
	}
	m.toolResults = map[string]any{}
	for _, msg := range req.Messages {
		for _, p := range msg.Content {
			if p.IsToolResponse() {
				m.toolResults[p.ToolResponse.Name] = p.ToolResponse.Output
			}
		}
	}
	if len(m.toolResults) == 0 {
		return &ai.ModelResponse{Message: ai.NewModelMessage(m.firstTurn...), FinishReason: ai.FinishReasonStop}, nil
	}
	data, err := json.Marshal(m.final(m.toolResults))
	if err != nil {
		m.t.Fatal(err)
	}
	return &ai.ModelResponse{Message: ai.NewModelTextMessage(string(data)), FinishReason: ai.FinishReasonStop}, nil
}

func setup(t *testing.T, m *scriptedModel, runner workers.Runner, client *http.Client, hosts ...string) *Analyzer {
	t.Helper()
	ctx := context.Background()
	g := genkit.Init(ctx)
	model := genkit.DefineModel(g, "teste/roteiro", &ai.ModelOptions{Supports: &ai.ModelSupports{
		Multiturn: true, Tools: true, SystemRole: true, Output: []string{"text", "json"},
	}}, m.generate)
	return New(g, model, Config{Runner: runner, AllowedHosts: hosts, HTTPClient: client, Now: func() time.Time { return today }})
}

func toolCall(name string, input map[string]any) *ai.Part {
	return ai.NewToolRequestPart(&ai.ToolRequest{Name: name, Ref: name, Input: input})
}

func TestModeloChamaToolsGoEPythonEUsaOResultado(t *testing.T) {
	runner := &fakeRunner{}
	m := &scriptedModel{t: t,
		firstTurn: []*ai.Part{
			toolCall("buscar_no_documento", map[string]any{"termos": []string{"inscrições", "vagas"}}),
			toolCall("extrair_regras", map[string]any{"texto": "Período de inscrições: 10/11/2026 a 16/11/2026"}),
			toolCall("situacao_inscricao", map[string]any{"inicio": "2026-11-10", "fim": "2026-11-16"}),
		},
		final: func(r map[string]any) Output {
			search, _ := json.Marshal(r["buscar_no_documento"])
			if !strings.Contains(string(search), "10/11/2026 a 16/11/2026") {
				t.Errorf("busca não trouxe o período: %s", search)
			}
			situation, _ := json.Marshal(r["situacao_inscricao"])
			if !strings.Contains(string(situation), `"prevista"`) {
				t.Errorf("situacao_inscricao = %s", situation)
			}
			return Output{Modalidade: "admissao_graduados", InscricaoInicio: "2026-11-10", InscricaoFim: "2026-11-16",
				Status: "aberta", OfereceCC: true, CCCuiaba: true, VagasCC: 4, Resumo: "Inscrições em novembro.",
				Evidencia: "Período de inscrições: 10/11/2026 a 16/11/2026"}
		},
	}
	a := setup(t, m, runner, nil)
	resp, err := a.Analyze(context.Background(), &ufmtv1.AnalyzeRequest{Title: "Edital 09/2026", Body: edital})
	if err != nil {
		t.Fatal(err)
	}
	got := resp.Analysis
	if got.Status != "prevista" {
		t.Errorf("status = %q; o código deve recalcular a partir das datas (o modelo disse aberta)", got.Status)
	}
	if got.InscricaoInicio != "2026-11-10" || got.VagasCienciaComputacaoCuiaba != 4 || !got.CienciaComputacaoCuiaba {
		t.Errorf("análise inesperada: %+v", got)
	}
	for _, tool := range []string{"buscar_no_documento", "extrair_regras", "situacao_inscricao"} {
		if !slices.Contains(resp.ToolsUsed, tool) {
			t.Errorf("tool %s não registrada em %v", tool, resp.ToolsUsed)
		}
	}
	if !slices.Equal(runner.calls, []workers.Name{workers.Python}) {
		t.Errorf("workers chamados = %v", runner.calls)
	}
	want := []string{"buscar_no_documento", "ler_trecho", "situacao_inscricao", "extrair_regras", "ler_publicacao", "ocr_pdf", "renderizar_pagina"}
	for _, name := range want {
		if !slices.Contains(m.offered, name) {
			t.Errorf("tool %s não foi oferecida ao modelo (%v)", name, m.offered)
		}
	}
}

func TestOcrBaixaPDFDaUFMTEChamaOWorkerC(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("%PDF-1.4 escaneado"))
	}))
	defer srv.Close()
	runner := &fakeRunner{}
	m := &scriptedModel{t: t,
		firstTurn: []*ai.Part{toolCall("ocr_pdf", map[string]any{"url": srv.URL + "/cronograma.pdf"})},
		final: func(r map[string]any) Output {
			out, _ := json.Marshal(r["ocr_pdf"])
			if !strings.Contains(string(out), "cronograma escaneado") {
				t.Errorf("ocr_pdf = %s", out)
			}
			return Output{Modalidade: "admissao_graduados", Status: "indeterminada", VagasCC: -1}
		},
	}
	a := setup(t, m, runner, srv.Client(), "127.0.0.1")
	if _, err := a.Analyze(context.Background(), &ufmtv1.AnalyzeRequest{Body: edital}); err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(runner.calls, []workers.Name{workers.OCR}) {
		t.Errorf("workers chamados = %v", runner.calls)
	}
}

func TestToolsNaoAcessamDominiosForaDaUFMT(t *testing.T) {
	runner := &fakeRunner{}
	m := &scriptedModel{t: t,
		firstTurn: []*ai.Part{toolCall("ler_publicacao", map[string]any{"url": "https://exemplo.com/roubar"})},
		final: func(r map[string]any) Output {
			out, _ := json.Marshal(r["ler_publicacao"])
			if !strings.Contains(string(out), "não permitido") {
				t.Errorf("esperava recusa do domínio, veio %s", out)
			}
			return Output{Modalidade: "outro", Status: "indeterminada", VagasCC: -1}
		},
	}
	a := setup(t, m, runner, nil)
	if _, err := a.Analyze(context.Background(), &ufmtv1.AnalyzeRequest{Body: edital}); err != nil {
		t.Fatal(err)
	}
	if len(runner.calls) != 0 {
		t.Errorf("nenhum worker deveria rodar, rodaram %v", runner.calls)
	}
}

func TestNormalizeNaoConfiaNoModelo(t *testing.T) {
	a := normalize(&Output{Modalidade: "inventada", InscricaoInicio: "31/12/2026", Status: "talvez", VagasCC: -7}, today)
	if a.Modalidade != "outro" || a.InscricaoInicio != "" || a.Status != "indeterminada" || a.VagasCienciaComputacaoCuiaba != -1 {
		t.Errorf("normalize = %+v", a)
	}
}

func TestNormalizeDescartaPeriodoPelaMetade(t *testing.T) {
	cases := []Output{
		{InscricaoInicio: "2026-13-45", InscricaoFim: "2026-11-30", Status: "aberta", VagasCC: 5000000000},
		{InscricaoInicio: "2026-11-30", InscricaoFim: "2026-10-01", Status: "aberta", VagasCC: -1},
	}
	for _, o := range cases {
		a := normalize(&o, today)
		if a.InscricaoInicio != "" || a.InscricaoFim != "" || a.Status != "indeterminada" || a.VagasCienciaComputacaoCuiaba != -1 {
			t.Errorf("normalize(%+v) = %+v", o, a)
		}
	}
	if a := normalize(&Output{InscricaoFim: "2026-10-30", VagasCC: 4}, today); a.Status != "aberta" || a.VagasCienciaComputacaoCuiaba != 4 {
		t.Errorf("só o fim, válido, continua valendo: %+v", a)
	}
}

func TestDownloadNaoSegueRedirectParaForaDaUFMT(t *testing.T) {
	evil := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("%PDF")) }))
	defer evil.Close()
	ufmt := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, evil.URL, http.StatusFound) // evil responde em 127.0.0.1
	}))
	defer ufmt.Close()
	g := genkit.Init(context.Background())
	ts := defineTools(g, &fakeRunner{}, []string{"localhost"}, http.DefaultClient)
	local := strings.Replace(ufmt.URL, "127.0.0.1", "localhost", 1)
	if _, err := ts.download(context.Background(), local); err == nil || !strings.Contains(err.Error(), "não permitido") {
		t.Fatalf("redirect para host fora da lista deveria falhar, err=%v", err)
	}
	if http.DefaultClient.CheckRedirect != nil {
		t.Error("não pode alterar o client recebido")
	}
}

func TestDocumentSearchIgnoraAcentoEDevolvePosicao(t *testing.T) {
	d := NewDocument(edital)
	got := d.Search([]string{"INSCRICOES"}, 10, 5)
	if len(got) != 2 {
		t.Fatalf("esperava 2 ocorrências, veio %d: %+v", len(got), got)
	}
	if !strings.Contains(d.Read(got[0].Posicao, 40), "inscrições") {
		t.Errorf("posição não aponta para o trecho: %q", d.Read(got[0].Posicao, 40))
	}
}

func TestSituation(t *testing.T) {
	cases := map[[2]string]string{
		{"2026-06-23", "2026-06-28"}: "encerrada",
		{"2026-10-20", "2026-10-30"}: "aberta",
		{"2026-11-10", "2026-11-16"}: "prevista",
		{"", "2026-11-05"}:           "aberta",
		{"", ""}:                     "indeterminada",
	}
	for in, want := range cases {
		if got := Situation(in[0], in[1], today); got != want {
			t.Errorf("Situation(%v) = %s, esperava %s", in, got, want)
		}
	}
}
