// ufmt-ai analisa publicações da UFMT com o Groq (via Genkit), usando como
// tools os workers Python, JavaScript e C do projeto.
//
//	ufmt-ai --worker                        Request protobuf no stdin, Response no stdout
//	ufmt-ai analyze -file edital.txt [...]  resultado em JSON no stdout
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"github.com/firebase/genkit/go/genkit"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"

	ufmtv1 "github.com/gustavoz65/Chrono-BOT/ai/gen/ufmtv1"
	"github.com/gustavoz65/Chrono-BOT/ai/groq"
	"github.com/gustavoz65/Chrono-BOT/ai/internal/agent"
	"github.com/gustavoz65/Chrono-BOT/ai/internal/workers"
)

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func newAnalyzer(ctx context.Context) (*agent.Analyzer, error) {
	if os.Getenv("GROQ_API_KEY") == "" {
		return nil, groq.ErrMissingAPIKey
	}
	root, err := workers.FindRoot()
	if err != nil {
		return nil, err
	}
	model := env("GROQ_MODEL", groq.DefaultModel)
	cfg := &groq.ChatConfig{MaxOutputTokens: 4000}
	if strings.HasPrefix(model, "openai/gpt-oss") {
		cfg.ReasoningEffort = groq.ReasoningEffortLow
	}
	var hosts []string
	if raw := os.Getenv("AI_ALLOWED_HOSTS"); raw != "" {
		hosts = strings.Split(raw, ",")
	}
	g := genkit.Init(ctx, genkit.WithPlugins(&groq.Groq{}))
	return agent.New(g, groq.ModelRef(model, cfg), agent.Config{Runner: workers.NewExec(root), AllowedHosts: hosts}), nil
}

func timeout() time.Duration {
	d, err := time.ParseDuration(env("AI_TIMEOUT", "3m"))
	if err != nil || d <= 0 {
		return 3 * time.Minute
	}
	return d
}

func analyze(ctx context.Context, req *ufmtv1.AnalyzeRequest) (*ufmtv1.AnalyzeResponse, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout())
	defer cancel()
	a, err := newAnalyzer(ctx)
	if err != nil {
		return nil, err
	}
	return a.Analyze(ctx, req)
}

func errorResponse(code ufmtv1.Error_Code, err error) *ufmtv1.Response {
	return &ufmtv1.Response{Error: &ufmtv1.Error{Code: code, Message: err.Error()}}
}

func handle(ctx context.Context, input []byte) *ufmtv1.Response {
	req := &ufmtv1.Request{}
	if err := proto.Unmarshal(input, req); err != nil {
		return errorResponse(ufmtv1.Error_CODE_INVALID_REQUEST, fmt.Errorf("Request protobuf inválido: %w", err))
	}
	if req.GetAnalyze() == nil {
		return errorResponse(ufmtv1.Error_CODE_UNSUPPORTED, errors.New("este worker só atende AnalyzeRequest"))
	}
	resp, err := analyze(ctx, req.GetAnalyze())
	switch {
	case errors.Is(err, groq.ErrMissingAPIKey):
		return errorResponse(ufmtv1.Error_CODE_UNAVAILABLE, err)
	case err != nil:
		return errorResponse(ufmtv1.Error_CODE_INTERNAL, err)
	}
	return &ufmtv1.Response{Payload: &ufmtv1.Response_Analyze{Analyze: resp}}
}

func worker(ctx context.Context) error {
	input, err := io.ReadAll(os.Stdin)
	if err != nil {
		return err
	}
	out, err := proto.Marshal(handle(ctx, input))
	if err != nil {
		return err
	}
	_, err = os.Stdout.Write(out)
	return err
}

func cli(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("analyze", flag.ContinueOnError)
	file := fs.String("file", "", "arquivo de texto com o conteúdo da publicação (- para stdin)")
	title := fs.String("title", "", "título da publicação")
	url := fs.String("url", "", "URL da publicação")
	today := fs.String("today", "", "data de referência AAAA-MM-DD (padrão: hoje)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	var body []byte
	var err error
	switch *file {
	case "":
		return errors.New("informe -file")
	case "-":
		body, err = io.ReadAll(os.Stdin)
	default:
		body, err = os.ReadFile(*file)
	}
	if err != nil {
		return err
	}
	resp, err := analyze(ctx, &ufmtv1.AnalyzeRequest{Title: *title, Url: *url, Body: string(body), Today: *today})
	if err != nil {
		return err
	}
	out, err := protojson.MarshalOptions{Multiline: true, EmitUnpopulated: true}.Marshal(resp)
	if err != nil {
		return err
	}
	fmt.Println(string(out))
	return nil
}

func main() {
	ctx := context.Background()
	var err error
	switch {
	case len(os.Args) == 2 && os.Args[1] == "--worker":
		err = worker(ctx)
	case len(os.Args) >= 2 && os.Args[1] == "analyze":
		err = cli(ctx, os.Args[2:])
	default:
		fmt.Fprintln(os.Stderr, "uso: ufmt-ai --worker | ufmt-ai analyze -file arquivo [-title t] [-url u] [-today AAAA-MM-DD]")
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "ufmt-ai:", err)
		os.Exit(1)
	}
}
