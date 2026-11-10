package main

import (
	"errors"
	"os"

	"octop.desktop/internal/fileops"
)

var (
	errPPTXOutputFolder = errors.New("PPTX_OUTPUT_FOLDER")
	errPPTXOutputWrite  = errors.New("PPTX_OUTPUT_WRITE_FAILED")
)

func writeLocalPPTXOutput(s docxSession, root *os.Root, name string, data []byte, beforePublish func() error) error {
	directory, err := root.Open(".")
	if err != nil {
		return errPPTXOutputFolder
	}
	defer directory.Close()
	apply := func(publish func() error) error {
		if err := beforePublish(); err != nil {
			return err
		}
		commit := func() error {
			if !fileops.SameDirectory(root, root.Name()) {
				return errPPTXOutputFolder
			}
			err := publish()
			if errors.Is(err, os.ErrExist) {
				return errors.New("PPTX_CREATE_ONLY")
			}
			if err != nil {
				return errPPTXOutputWrite
			}
			return nil
		}
		if s.Apply != nil {
			return s.Apply(commit)
		}
		return commit()
	}
	return writeStagedPPTX(root, directory, name, data, apply)
}

func syncPPTXOutput(file *os.File, data []byte) error {
	if _, err := file.Write(data); err != nil {
		return err
	}
	return file.Sync()
}
