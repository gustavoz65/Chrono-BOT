package groq

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/firebase/genkit/go/ai"
	"github.com/firebase/genkit/go/genkit"
)

type somaIn struct {
	A int `json:"a"`
	B int `json:"b"`
}

// fakeGroq responde no formato da API do Groq (compatível com a da OpenAI).
func fakeGroq(t *testing.T) (*httptest.Server, *[]map[string]any) {
	var mu sync.Mutex
	var bodies []map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/chat/completions" {
			t.Errorf("caminho inesperado: %s", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer chave-teste" {
			t.Errorf("Authorization = %q", got)
		}
		raw, _ := io.ReadAll(r.Body)
		var body map[string]any
		_ = json.Unmarshal(raw, &body)
		mu.Lock()
		bodies = append(bodies, body)
		turn := len(bodies)
		mu.Unlock()

		w.Header().Set("Content-Type", "application/json")
		message := `{"role":"assistant","content":null,"tool_calls":[{"id":"call_1","type":"function",` +
			`"function":{"name":"soma","arguments":"{\"a\":2,\"b\":3}"}}]}`
		finish := "tool_calls"
		if turn > 1 {
			message, finish = `{"role":"assistant","content":"O resultado é 5."}`, "stop"
		}
		_, _ = io.WriteString(w, `{"id":"x","object":"chat.completion","created":1,"model":"openai/gpt-oss-120b",`+
			`"choices":[{"index":0,"finish_reason":"`+finish+`","message":`+message+`}],`+
			`"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}`)
	}))
	return srv, &bodies
}

func TestPluginExecutaToolPedidaPeloModelo(t *testing.T) {
	srv, bodies := fakeGroq(t)
	defer srv.Close()

	ctx := context.Background()
	g := genkit.Init(ctx, genkit.WithPlugins(&Groq{APIKey: "chave-teste", BaseURL: srv.URL}))
	called := false
	soma := genkit.DefineTool(g, "soma", "Soma dois inteiros.", func(_ *ai.ToolContext, in somaIn) (int, error) {
		called = true
		return in.A + in.B, nil
	})

	resp, err := genkit.Generate(ctx, g,
		ai.WithModel(ModelRef(DefaultModel, &ChatConfig{ReasoningEffort: ReasoningEffortLow, MaxOutputTokens: 500})),
		ai.WithPrompt("Quanto é 2 + 3?"),
		ai.WithTools(soma),
	)
	if err != nil {
		t.Fatal(err)
	}
	if !called {
		t.Fatal("a tool soma não foi executada")
	}
	if resp.Text() != "O resultado é 5." {
		t.Errorf("texto = %q", resp.Text())
	}
	if len(*bodies) != 2 {
		t.Fatalf("esperava 2 chamadas à API, houve %d", len(*bodies))
	}
	first := (*bodies)[0]
	if first["model"] != DefaultModel || first["reasoning_effort"] != "low" || first["max_completion_tokens"] != float64(500) {
		t.Errorf("parâmetros enviados: model=%v reasoning_effort=%v max_completion_tokens=%v",
			first["model"], first["reasoning_effort"], first["max_completion_tokens"])
	}
	if tools, _ := first["tools"].([]any); len(tools) != 1 {
		t.Errorf("tools enviadas = %v", first["tools"])
	}
	second, _ := json.Marshal((*bodies)[1]["messages"])
	if !strings.Contains(string(second), `"tool_call_id":"call_1"`) || !strings.Contains(string(second), "5") {
		t.Errorf("o resultado da tool não voltou para o modelo: %s", second)
	}
}

func TestInitSemChavePanicaComErroClaro(t *testing.T) {
	t.Setenv("GROQ_API_KEY", "")
	defer func() {
		if r := recover(); r != ErrMissingAPIKey {
			t.Errorf("recover = %v", r)
		}
	}()
	(&Groq{}).Init(context.Background())
}
