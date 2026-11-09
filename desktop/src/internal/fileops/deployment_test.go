package fileops

import (
	"encoding/xml"
	"os"
	"testing"
)

func TestManagedPolicyRequiresFreshInteractiveConfirmation(t *testing.T) {
	data, err := os.ReadFile("../../build/linux/fileops/org.colink.fileops.policy")
	if err != nil {
		t.Fatal(err)
	}
	var policy struct {
		Actions []struct {
			ID       string   `xml:"id,attr"`
			Messages []string `xml:"message"`
			Defaults struct {
				Any      string `xml:"allow_any"`
				Inactive string `xml:"allow_inactive"`
				Active   string `xml:"allow_active"`
			} `xml:"defaults"`
			Owner struct {
				Key   string `xml:"key,attr"`
				Value string `xml:",chardata"`
			} `xml:"annotate"`
		} `xml:"action"`
	}
	if err = xml.Unmarshal(data, &policy); err != nil {
		t.Fatal(err)
	}
	want := map[string]bool{OverwriteAction: true, DeleteAction: true}
	for _, action := range policy.Actions {
		if !want[action.ID] || len(action.Messages) != 2 {
			t.Fatalf("unexpected action or missing localized messages: %s", action.ID)
		}
		delete(want, action.ID)
		if action.Defaults.Any != "no" || action.Defaults.Inactive != "no" || action.Defaults.Active != "auth_self" {
			t.Errorf("%s can skip fresh session confirmation", action.ID)
		}
		if action.Owner.Key != "org.freedesktop.policykit.owner" || action.Owner.Value != "unix-user:colink-fileops" {
			t.Errorf("%s does not limit prompt authority to the service identity", action.ID)
		}
		if action.Messages[0] != "$(prompt_en)" || action.Messages[1] != "$(prompt_zh)" {
			t.Errorf("%s must keep each policy message in a single text segment: %q", action.ID, action.Messages)
		}
	}
	if len(want) != 0 {
		t.Fatalf("missing actions: %v", want)
	}
}
