// Package workers executa os módulos das outras linguagens (Python, JavaScript
// e C) como processos: um ufmtv1.Request no stdin, um ufmtv1.Response no stdout.
package workers

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"google.golang.org/protobuf/proto"

	ufmtv1 "github.com/gustavoz65/Chrono-BOT/ai/gen/ufmtv1"
)

type Name string

const (
	Python  Name = "python"
	Browser Name = "browser"
	OCR     Name = "ocr"
)

type Runner interface {
	Call(ctx context.Context, worker Name, req *ufmtv1.Request) (*ufmtv1.Response, error)
}

type Error struct {
	Worker  Name
	Code    ufmtv1.Error_Code
	Message string
}

func (e *Error) Error() string { return fmt.Sprintf("%s: %s", e.Worker, e.Message) }

const marker = "proto/ufmt/v1/worker.proto"

// FindRoot localiza a raiz do repositório: UFMT_ROOT, ou subindo a partir do
// diretório atual e do executável até achar o contrato protobuf.
func FindRoot() (string, error) {
	if root := os.Getenv("UFMT_ROOT"); root != "" {
		return root, nil
	}
	var starts []string
	if wd, err := os.Getwd(); err == nil {
		starts = append(starts, wd)
	}
	if exe, err := os.Executable(); err == nil {
		starts = append(starts, filepath.Dir(exe))
	}
	for _, dir := range starts {
		for {
			if _, err := os.Stat(filepath.Join(dir, marker)); err == nil {
				return dir, nil
			}
			parent := filepath.Dir(dir)
			if parent == dir {
				break
			}
			dir = parent
		}
	}
	return "", errors.New("raiz do projeto não encontrada; defina UFMT_ROOT")
}

type Exec struct {
	Root     string
	Python   string
	Node     string
	Timeouts map[Name]time.Duration
}

func NewExec(root string) *Exec {
	python := os.Getenv("PYTHON")
	if python == "" {
		python = "python3"
	}
	return &Exec{
		Root:   root,
		Python: python,
		Node:   "node",
		Timeouts: map[Name]time.Duration{
			Python: 3 * time.Minute, Browser: 3 * time.Minute, OCR: 5 * time.Minute,
		},
	}
}

func (e *Exec) command(name Name) (string, []string, error) {
	switch name {
	case Python:
		return e.Python, []string{"-m", "ufmt_monitor.worker"}, nil
	case Browser:
		return e.Node, []string{filepath.Join(e.Root, "browser", "src", "cli.js"), "--worker"}, nil
	case OCR:
		return filepath.Join(e.Root, "ocr", "bin", "ufmt-ocr"), []string{"--worker"}, nil
	}
	return "", nil, fmt.Errorf("worker desconhecido: %q", name)
}

func (e *Exec) Call(ctx context.Context, name Name, req *ufmtv1.Request) (*ufmtv1.Response, error) {
	bin, args, err := e.command(name)
	if err != nil {
		return nil, err
	}
	input, err := proto.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("%s: serializar request: %w", name, err)
	}
	if timeout := e.Timeouts[name]; timeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, timeout)
		defer cancel()
	}
	cmd := exec.CommandContext(ctx, bin, args...)
	cmd.Dir = e.Root
	cmd.Stdin = bytes.NewReader(input)
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		if ctx.Err() != nil {
			return nil, &Error{Worker: name, Code: ufmtv1.Error_CODE_UNAVAILABLE, Message: "tempo esgotado"}
		}
		return nil, &Error{Worker: name, Code: ufmtv1.Error_CODE_UNAVAILABLE,
			Message: fmt.Sprintf("%v: %s", err, tail(stderr.Bytes(), 300))}
	}
	resp := &ufmtv1.Response{}
	if err := proto.Unmarshal(stdout.Bytes(), resp); err != nil {
		return nil, &Error{Worker: name, Code: ufmtv1.Error_CODE_INTERNAL, Message: "resposta protobuf inválida"}
	}
	if resp.Error != nil {
		return nil, &Error{Worker: name, Code: resp.Error.Code, Message: resp.Error.Message}
	}
	return resp, nil
}

func tail(b []byte, n int) string {
	if len(b) > n {
		b = b[len(b)-n:]
	}
	return string(bytes.TrimSpace(b))
}
