package agent

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/firebase/genkit/go/ai"
	"github.com/firebase/genkit/go/genkit"

	ufmtv1 "github.com/gustavoz65/Chrono-BOT/ai/gen/ufmtv1"
	"github.com/gustavoz65/Chrono-BOT/ai/internal/workers"
)

const (
	maxSnippets   = 8
	snippetRadius = 250
	maxRead       = 3000
	maxToolText   = 6000
	maxPDFBytes   = 15 << 20
)

type sessionKey struct{}

// session carrega o documento da análise atual até as tools, que são
// registradas uma vez no Genkit e servem a todas as análises.
type session struct {
	doc   *Document
	today time.Time
	mu    sync.Mutex
	used  []string
}

func withSession(ctx context.Context, s *session) context.Context {
	return context.WithValue(ctx, sessionKey{}, s)
}

func current(ctx context.Context) (*session, error) {
	s, ok := ctx.Value(sessionKey{}).(*session)
	if !ok {
		return nil, errors.New("tool chamada fora de uma análise")
	}
	return s, nil
}

func (s *session) record(tool string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !slices.Contains(s.used, tool) {
		s.used = append(s.used, tool)
	}
}

func truncate(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n]) + " […]"
}

type SearchInput struct {
	Termos []string `json:"termos" jsonschema_description:"Palavras ou expressões a procurar, sem diferenciar acentos e maiúsculas (ex.: inscrição, período, vagas, Ciência da Computação)."`
}

// ToolError faz a falha de uma tool chegar ao modelo como dado, para ele
// poder seguir por outro caminho em vez de a análise inteira abortar.
type ToolError struct {
	Erro string `json:"erro,omitempty" jsonschema_description:"Preenchido quando a tool falhou; tente outro caminho."`
}

func (e *ToolError) fail(msg string) { e.Erro = msg }

type failable interface{ fail(string) }

type SearchOutput struct {
	ToolError
	Trechos []Snippet `json:"trechos"`
	Total   int       `json:"tamanho_documento"`
}

type ReadInput struct {
	Posicao int `json:"posicao" jsonschema_description:"Posição inicial, em caracteres."`
	Tamanho int `json:"tamanho" jsonschema_description:"Quantidade de caracteres (máximo 3000)."`
}

type ReadOutput struct {
	ToolError
	Texto string `json:"texto"`
}

type RulesInput struct {
	Texto string `json:"texto" jsonschema_description:"Trecho do documento a analisar com as regras determinísticas do projeto."`
}

type SituationInput struct {
	Inicio string `json:"inicio" jsonschema_description:"Início das inscrições, AAAA-MM-DD, ou vazio."`
	Fim    string `json:"fim" jsonschema_description:"Fim das inscrições, AAAA-MM-DD, ou vazio."`
}

type SituationOutput struct {
	ToolError
	Situacao string `json:"situacao" jsonschema:"enum=aberta,enum=prevista,enum=encerrada,enum=indeterminada"`
	Hoje     string `json:"hoje"`
}

type URLInput struct {
	URL string `json:"url" jsonschema_description:"Endereço http(s) de uma página ou PDF da UFMT."`
}

type RulesOutput struct {
	ToolError
	Regras *ufmtv1.ExtractResponse `json:"regras,omitempty"`
}

type FetchOutput struct {
	ToolError
	Texto        string   `json:"texto"`
	Fontes       []string `json:"fontes"`
	Verificacoes []string `json:"verificacoes"`
	TextoCortado bool     `json:"texto_cortado"`
}

type OCROutput struct {
	ToolError
	Paginas []OCRPage `json:"paginas"`
}

type OCRPage struct {
	Numero    int    `json:"numero"`
	Confianca int    `json:"confianca"`
	Texto     string `json:"texto"`
}

type RenderOutput struct {
	ToolError
	URLFinal string `json:"url_final"`
	Texto    string `json:"texto"`
}

type toolset struct {
	runner workers.Runner
	hosts  []string
	client *http.Client
	refs   []ai.ToolRef
}

func (t *toolset) allowed(raw string) (*url.URL, error) {
	u, err := url.Parse(raw)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") {
		return nil, fmt.Errorf("url inválida: %q", raw)
	}
	host := strings.ToLower(u.Hostname())
	for _, h := range t.hosts {
		if host == h || strings.HasSuffix(host, "."+h) {
			return u, nil
		}
	}
	return nil, fmt.Errorf("domínio não permitido: %s (permitidos: %s)", host, strings.Join(t.hosts, ", "))
}

func define[In, Out any, P interface {
	*Out
	failable
}](g *genkit.Genkit, name, description string, fn func(*ai.ToolContext, *session, In) (Out, error)) ai.ToolRef {
	return genkit.DefineTool(g, name, description, func(tc *ai.ToolContext, in In) (Out, error) {
		var zero Out
		s, err := current(tc)
		if err != nil {
			return zero, err
		}
		s.record(name)
		out, err := fn(tc, s, in)
		if err != nil {
			P(&zero).fail(err.Error())
			return zero, nil
		}
		return out, nil
	})
}

func defineTools(g *genkit.Genkit, runner workers.Runner, hosts []string, client *http.Client) *toolset {
	t := &toolset{runner: runner, hosts: hosts, client: client}

	t.refs = append(t.refs, define(g, "buscar_no_documento",
		"Procura termos no documento em análise e devolve trechos ao redor de cada ocorrência.",
		func(_ *ai.ToolContext, s *session, in SearchInput) (SearchOutput, error) {
			return SearchOutput{Trechos: s.doc.Search(in.Termos, snippetRadius, maxSnippets), Total: s.doc.Len()}, nil
		}))

	t.refs = append(t.refs, define(g, "ler_trecho",
		"Lê um pedaço contínuo do documento em análise a partir de uma posição.",
		func(_ *ai.ToolContext, s *session, in ReadInput) (ReadOutput, error) {
			return ReadOutput{Texto: s.doc.Read(in.Posicao, min(max0(in.Tamanho), maxRead))}, nil
		}))

	t.refs = append(t.refs, define(g, "situacao_inscricao",
		"Calcula se um período de inscrição está aberto, previsto ou encerrado em relação à data de hoje.",
		func(_ *ai.ToolContext, s *session, in SituationInput) (SituationOutput, error) {
			return SituationOutput{Situacao: Situation(in.Inicio, in.Fim, s.today), Hoje: s.today.Format(time.DateOnly)}, nil
		}))

	t.refs = append(t.refs, define(g, "extrair_regras",
		"Roda as regras determinísticas do projeto (Python) num trecho: datas, períodos de inscrição, "+
			"modalidade, status e vagas de Ciência da Computação em Cuiabá. Use para confirmar o que você leu.",
		func(tc *ai.ToolContext, s *session, in RulesInput) (RulesOutput, error) {
			resp, err := runner.Call(tc, workers.Python, &ufmtv1.Request{Payload: &ufmtv1.Request_Extract{
				Extract: &ufmtv1.ExtractRequest{Text: truncate(in.Texto, 20000), Today: s.today.Format(time.DateOnly)}}})
			if err != nil {
				return RulesOutput{}, err
			}
			return RulesOutput{Regras: resp.GetExtract()}, nil
		}))

	t.refs = append(t.refs, define(g, "ler_publicacao",
		"Baixa e lê outra página ou PDF da UFMT (por exemplo, um anexo citado no documento), com verificação "+
			"cruzada HTML×navegador e texto×OCR feita pelo módulo Python.",
		func(tc *ai.ToolContext, _ *session, in URLInput) (FetchOutput, error) {
			if _, err := t.allowed(in.URL); err != nil {
				return FetchOutput{}, err
			}
			resp, err := runner.Call(tc, workers.Python, &ufmtv1.Request{Payload: &ufmtv1.Request_Fetch{
				Fetch: &ufmtv1.FetchRequest{Url: in.URL}}})
			if err != nil {
				return FetchOutput{}, err
			}
			f := resp.GetFetch()
			out := FetchOutput{Texto: truncate(f.Text, maxToolText), Fontes: f.Sources,
				TextoCortado: len([]rune(f.Text)) > maxToolText}
			for _, c := range f.Checks {
				out.Verificacoes = append(out.Verificacoes, fmt.Sprintf("%s: %s %s", c.Name, c.Result, c.Detail))
			}
			return out, nil
		}))

	t.refs = append(t.refs, define(g, "ocr_pdf",
		"Baixa um PDF da UFMT e extrai o texto com OCR (módulo C + Tesseract). Use quando o PDF for escaneado.",
		func(tc *ai.ToolContext, _ *session, in URLInput) (OCROutput, error) {
			u, err := t.allowed(in.URL)
			if err != nil {
				return OCROutput{}, err
			}
			pdf, err := t.download(tc, u.String())
			if err != nil {
				return OCROutput{}, err
			}
			resp, err := runner.Call(tc, workers.OCR, &ufmtv1.Request{Payload: &ufmtv1.Request_Ocr{
				Ocr: &ufmtv1.OcrRequest{Source: &ufmtv1.OcrRequest_Pdf{Pdf: pdf}, Lang: "por", LastPage: 5}}})
			if err != nil {
				return OCROutput{}, err
			}
			var out OCROutput
			for _, p := range resp.GetOcr().GetPages() {
				out.Paginas = append(out.Paginas, OCRPage{Numero: int(p.Number), Confianca: int(p.Confidence),
					Texto: truncate(p.Text, maxToolText/2)})
			}
			return out, nil
		}))

	t.refs = append(t.refs, define(g, "renderizar_pagina",
		"Abre uma página da UFMT num navegador de verdade (módulo JavaScript) e devolve o texto visível, "+
			"inclusive o que só aparece com JavaScript.",
		func(tc *ai.ToolContext, _ *session, in URLInput) (RenderOutput, error) {
			if _, err := t.allowed(in.URL); err != nil {
				return RenderOutput{}, err
			}
			resp, err := runner.Call(tc, workers.Browser, &ufmtv1.Request{Payload: &ufmtv1.Request_Render{
				Render: &ufmtv1.RenderRequest{Urls: []string{in.URL}, TimeoutMs: 45000}}})
			if err != nil {
				return RenderOutput{}, err
			}
			pages := resp.GetRender().GetPages()
			if len(pages) == 0 {
				return RenderOutput{}, errors.New("navegador não devolveu página")
			}
			if pages[0].Error != "" {
				return RenderOutput{}, errors.New(pages[0].Error)
			}
			return RenderOutput{URLFinal: pages[0].FinalUrl, Texto: truncate(pages[0].Text, maxToolText)}, nil
		}))

	return t
}

func (t *toolset) download(ctx context.Context, raw string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, raw, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "UFMT-Graduados-Monitor/2.0 (uso pessoal)")
	resp, err := t.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d ao baixar %s", resp.StatusCode, raw)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxPDFBytes+1))
	if err != nil {
		return nil, err
	}
	if len(data) > maxPDFBytes {
		return nil, fmt.Errorf("PDF maior que %d MB", maxPDFBytes>>20)
	}
	return data, nil
}

func Situation(start, end string, today time.Time) string {
	parse := func(s string) (time.Time, bool) {
		t, err := time.Parse(time.DateOnly, s)
		return t, err == nil
	}
	s, hasStart := parse(start)
	e, hasEnd := parse(end)
	day := time.Date(today.Year(), today.Month(), today.Day(), 0, 0, 0, 0, time.UTC)
	switch {
	case !hasStart && !hasEnd:
		return "indeterminada"
	case hasEnd && e.Before(day):
		return "encerrada"
	case hasStart && s.After(day):
		return "prevista"
	default:
		return "aberta"
	}
}
