// Package groq é um plugin Genkit para os modelos do Groq, que expõe uma API
// compatível com a da OpenAI em https://api.groq.com/openai/v1.
package groq

import (
	"context"
	"errors"
	"os"

	"github.com/firebase/genkit/go/ai"
	"github.com/firebase/genkit/go/core/api"
	"github.com/firebase/genkit/go/plugins/compat_oai"
	"github.com/openai/openai-go"
	"github.com/openai/openai-go/option"
	"github.com/openai/openai-go/shared"
)

const (
	Provider       = "groq"
	DefaultBaseURL = "https://api.groq.com/openai/v1"
	DefaultModel   = "openai/gpt-oss-120b"
)

var ErrMissingAPIKey = errors.New("groq: defina GROQ_API_KEY ou Groq.APIKey")

type ReasoningEffort string

const (
	ReasoningEffortLow    ReasoningEffort = "low"
	ReasoningEffortMedium ReasoningEffort = "medium"
	ReasoningEffortHigh   ReasoningEffort = "high"
)

type ChatConfig struct {
	compat_oai.RequestConfig

	Temperature     *float64        `json:"temperature,omitempty" jsonschema:"minimum=0,maximum=2" jsonschema_description:"Aleatoriedade da amostragem, de 0 a 2."`
	MaxOutputTokens int             `json:"maxOutputTokens,omitempty" jsonschema:"minimum=1" jsonschema_description:"Limite de tokens gerados, enviado como max_completion_tokens."`
	ReasoningEffort ReasoningEffort `json:"reasoningEffort,omitempty" jsonschema:"enum=low,enum=medium,enum=high" jsonschema_description:"Esforço de raciocínio dos modelos gpt-oss."`
	Seed            *int64          `json:"seed,omitempty" jsonschema_description:"Semente para respostas mais reproduzíveis."`
}

func (c ChatConfig) ApplyToChatCompletion(params *openai.ChatCompletionNewParams) {
	c.ApplyVersion(params)
	if c.Temperature != nil {
		params.Temperature = openai.Float(*c.Temperature)
	}
	if c.MaxOutputTokens > 0 {
		params.MaxCompletionTokens = openai.Int(int64(c.MaxOutputTokens))
	}
	if c.ReasoningEffort != "" {
		params.ReasoningEffort = shared.ReasoningEffort(c.ReasoningEffort)
	}
	if c.Seed != nil {
		params.Seed = openai.Int(*c.Seed)
	}
}

var (
	// O Groq não aceita response_format json_schema junto com tools: com tools,
	// o Genkit passa a instruir o formato pelo prompt.
	reasoning = ai.ModelSupports{
		Multiturn: true, Tools: true, SystemRole: true, ToolChoice: true,
		Output: []string{"text", "json"}, Constrained: ai.ConstrainedSupportNoTools,
	}
	jsonModeOnly = ai.ModelSupports{
		Multiturn: true, Tools: true, SystemRole: true, ToolChoice: true,
		Output: []string{"text", "json"}, Constrained: ai.ConstrainedSupportNone,
	}
)

var supportedModels = map[string]ai.ModelOptions{
	"openai/gpt-oss-120b":                       {Label: "GPT-OSS 120B (Groq)", Supports: &reasoning},
	"openai/gpt-oss-20b":                        {Label: "GPT-OSS 20B (Groq)", Supports: &reasoning},
	"moonshotai/kimi-k2-instruct-0905":          {Label: "Kimi K2 Instruct (Groq)", Supports: &reasoning},
	"llama-3.3-70b-versatile":                   {Label: "Llama 3.3 70B (Groq)", Supports: &jsonModeOnly},
	"meta-llama/llama-4-scout-17b-16e-instruct": {Label: "Llama 4 Scout (Groq)", Supports: &reasoning},
}

var dynamicModelOptions = ai.ModelOptions{
	Supports: &jsonModeOnly,
	Versions: []string{},
	Stage:    ai.ModelStageStable,
}

type Groq struct {
	APIKey  string
	BaseURL string
	Opts    []option.RequestOption
	Models  map[string]ai.ModelOptions

	compat compat_oai.OpenAICompatible
}

func (g *Groq) Name() string { return Provider }

func (g *Groq) Init(ctx context.Context) []api.Action {
	key := g.APIKey
	if key == "" {
		key = os.Getenv("GROQ_API_KEY")
	}
	if key == "" {
		panic(ErrMissingAPIKey)
	}
	baseURL := g.BaseURL
	if baseURL == "" {
		baseURL = os.Getenv("GROQ_BASE_URL")
	}
	if baseURL == "" {
		baseURL = DefaultBaseURL
	}
	g.compat.Provider = Provider
	g.compat.Opts = append([]option.RequestOption{option.WithAPIKey(key), option.WithBaseURL(baseURL)}, g.Opts...)
	actions := g.compat.Init(ctx)
	for id := range supportedModels {
		actions = append(actions, compat_oai.NewChatModel[ChatConfig](&g.compat, id, g.modelOptions(id)))
	}
	return actions
}

func (g *Groq) modelOptions(id string) ai.ModelOptions {
	return compat_oai.ModelOptionsFor(Provider, id, supportedModels, dynamicModelOptions, g.Models)
}

func (g *Groq) ListActions(ctx context.Context) []api.ActionDesc {
	return compat_oai.ListChatActions[ChatConfig](ctx, &g.compat, g.modelOptions)
}

func (g *Groq) ResolveAction(atype api.ActionType, id string) api.Action {
	return compat_oai.ResolveChatAction[ChatConfig](&g.compat, atype, id, g.modelOptions)
}

func ModelRef(id string, config *ChatConfig) ai.ModelRef {
	return ai.NewModelRef(compat_oai.ActionName(Provider, id), config)
}
