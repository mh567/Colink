package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"os/signal"
	"syscall"

	"octop.desktop/internal/fileops"
)

const configDirectory = "/etc/colink-fileops"

func trustedServerURL() (string, error) {
	invalid := errors.New("Configure the trusted Colink HTTPS server with the managed file service installer")
	info, err := os.Lstat(configDirectory)
	if err != nil || !info.IsDir() || info.Mode().Perm()&0022 != 0 || info.Sys().(*syscall.Stat_t).Uid != 0 {
		return "", invalid
	}
	f, err := os.OpenFile(configDirectory+"/server-url", os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return "", invalid
	}
	defer f.Close()
	info, err = f.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0022 != 0 || info.Sys().(*syscall.Stat_t).Uid != 0 {
		return "", invalid
	}
	data, err := io.ReadAll(io.LimitReader(f, 4097))
	if err != nil || len(data) > 4096 {
		return "", invalid
	}
	server, err := fileops.NormalizeServerURL(string(data))
	if err != nil {
		return "", invalid
	}
	return server, nil
}

func main() {
	if os.Geteuid() == 0 {
		log.Fatal("Run the managed file service with its dedicated system account.")
	}
	if len(os.Args) == 3 && os.Args[1] == "--check-server-url" {
		server, err := fileops.NormalizeServerURL(os.Args[2])
		if err != nil {
			log.Fatal("Enter the trusted Colink HTTPS server URL.")
		}
		fmt.Println(server)
		return
	}
	if len(os.Args) != 1 {
		log.Fatal("Start the managed file service with systemctl.")
	}
	server, err := trustedServerURL()
	if err != nil {
		log.Fatal(err)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if err := fileops.Serve(ctx, server); err != nil {
		log.Fatalf("Check the managed file service installation: %v", err)
	}
}
