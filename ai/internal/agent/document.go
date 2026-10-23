package agent

import (
	"strings"
	"unicode"
)

var accents = map[rune]rune{
	'á': 'a', 'à': 'a', 'â': 'a', 'ã': 'a', 'é': 'e', 'ê': 'e', 'í': 'i', 'ó': 'o', 'ô': 'o', 'õ': 'o',
	'ú': 'u', 'ü': 'u', 'ç': 'c',
}

// fold mantém um rune por rune, então posições no texto dobrado valem no original.
func fold(rs []rune) []rune {
	out := make([]rune, len(rs))
	for i, r := range rs {
		r = unicode.ToLower(r)
		if a, ok := accents[r]; ok {
			r = a
		}
		out[i] = r
	}
	return out
}

type Document struct {
	runes  []rune
	folded []rune
}

func NewDocument(text string) *Document {
	rs := []rune(text)
	return &Document{runes: rs, folded: fold(rs)}
}

func (d *Document) Len() int { return len(d.runes) }

type Snippet struct {
	Posicao int    `json:"posicao" jsonschema_description:"Posição (em caracteres) onde o trecho começa; use em ler_trecho."`
	Termo   string `json:"termo"`
	Trecho  string `json:"trecho"`
}

func indexRunes(hay, needle []rune, from int) int {
	for i := from; i+len(needle) <= len(hay); i++ {
		match := true
		for j := range needle {
			if hay[i+j] != needle[j] {
				match = false
				break
			}
		}
		if match {
			return i
		}
	}
	return -1
}

// Search devolve trechos ao redor das ocorrências dos termos, sem repetir
// regiões já cobertas, até max trechos.
func (d *Document) Search(terms []string, radius, max int) []Snippet {
	var out []Snippet
	covered := func(pos int) bool {
		for _, s := range out {
			if pos >= s.Posicao && pos < s.Posicao+len([]rune(s.Trecho)) {
				return true
			}
		}
		return false
	}
	for _, term := range terms {
		needle := fold([]rune(strings.TrimSpace(term)))
		if len(needle) == 0 {
			continue
		}
		for pos := indexRunes(d.folded, needle, 0); pos >= 0 && len(out) < max; pos = indexRunes(d.folded, needle, pos+len(needle)) {
			if covered(pos) {
				continue
			}
			start, end := max0(pos-radius), min(len(d.runes), pos+len(needle)+radius)
			out = append(out, Snippet{Posicao: start, Termo: term, Trecho: string(d.runes[start:end])})
		}
	}
	return out
}

func (d *Document) Read(start, size int) string {
	start = min(max0(start), len(d.runes))
	return string(d.runes[start:min(len(d.runes), start+max0(size))])
}

func max0(n int) int {
	if n < 0 {
		return 0
	}
	return n
}
