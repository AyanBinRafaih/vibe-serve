package main

import (
	"crypto/sha256"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
)

var (
	runnerOnce sync.Once
	runnerPath string
	runnerErr  error
)

func nativeRunnerPath() (string, error) {
	runnerOnce.Do(func() {
		if configured := os.Getenv("VIBESYS_QUEUE_NATIVE_RUNNER"); configured != "" {
			runnerPath, runnerErr = filepath.Abs(configured)
			if runnerErr == nil {
				runnerErr = validateRunnerExecutable(runnerPath)
			}
			return
		}

		cwd, err := os.Getwd()
		if err != nil {
			runnerErr = fmt.Errorf("resolve native runner source: %w", err)
			return
		}
		source := filepath.Join(cwd, "native_runner")
		sourceManifest := filepath.Join(source, "Cargo.toml")
		if _, err := os.Stat(sourceManifest); err != nil {
			runnerErr = fmt.Errorf("native runner manifest %q: %w", sourceManifest, err)
			return
		}
		isolatedSource, err := os.MkdirTemp("", "vibesys-queue-native-source-")
		if err != nil {
			runnerErr = fmt.Errorf("create isolated native runner source: %w", err)
			return
		}
		defer os.RemoveAll(isolatedSource)
		if err := copySourceTree(source, isolatedSource); err != nil {
			runnerErr = fmt.Errorf("copy isolated native runner source: %w", err)
			return
		}
		manifest := filepath.Join(isolatedSource, "Cargo.toml")

		digest := sha256.Sum256([]byte(source))
		target := filepath.Join(
			os.TempDir(),
			fmt.Sprintf("vibesys-queue-native-%x", digest[:8]),
		)
		command := exec.Command(
			"cargo",
			"build",
			"--quiet",
			"--release",
			"--locked",
			"--manifest-path",
			manifest,
			"--target-dir",
			target,
		)
		// Cargo discovers .cargo/config.toml in ancestor directories. Build from
		// the isolated copy so candidate workspace configuration cannot affect
		// compilation of this trusted runner.
		command.Dir = isolatedSource
		log := newBoundedLog(64 * 1024)
		command.Stdout = log
		command.Stderr = log
		if err := command.Run(); err != nil {
			runnerErr = fmt.Errorf(
				"build trusted native runner: %w\ncargo output:\n%s",
				err,
				log.String(),
			)
			return
		}
		runnerPath = filepath.Join(target, "release", "vibesys-queue-native-runner")
		runnerErr = validateRunnerExecutable(runnerPath)
	})
	return runnerPath, runnerErr
}

func copySourceTree(source, destination string) error {
	return filepath.WalkDir(source, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		relative, err := filepath.Rel(source, path)
		if err != nil {
			return err
		}
		target := filepath.Join(destination, relative)
		if entry.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		if !entry.Type().IsRegular() {
			return fmt.Errorf("unsupported native runner source file: %s", path)
		}
		contents, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		return os.WriteFile(target, contents, info.Mode().Perm())
	})
}

func validateRunnerExecutable(path string) error {
	stat, err := os.Stat(path)
	if err != nil {
		return fmt.Errorf("trusted native runner %q: %w", path, err)
	}
	if stat.IsDir() || stat.Mode()&0o111 == 0 {
		return fmt.Errorf("trusted native runner %q is not executable", path)
	}
	return nil
}
