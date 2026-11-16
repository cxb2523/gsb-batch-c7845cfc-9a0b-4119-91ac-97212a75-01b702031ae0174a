package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/segmentio/kafka-go"
)

// fakeReddit is a local stand-in for the Reddit OAuth + API endpoints.
//
// Behavior by subreddit:
//   - "ok":      first search request returns 401 (to exercise re-auth+retry),
//     then succeeds with posts p1 (comments OK), p2 (comments endpoint 500),
//     p3 (empty post, skipped).
//   - "limited": search always returns 429.
//   - "badjson": search returns 200 with an invalid JSON body.
//   - anything else: empty search result.
type fakeReddit struct {
	mu           sync.Mutex
	authCalls    int
	search401    map[string]bool // subreddit -> 401 already served
	commentCalls map[string]int  // postID -> comment endpoint hits
	server       *httptest.Server
}

func newFakeReddit(t *testing.T) *fakeReddit {
	t.Helper()
	f := &fakeReddit{
		search401:    make(map[string]bool),
		commentCalls: make(map[string]int),
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/access_token", func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		f.authCalls++
		n := f.authCalls
		f.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"access_token":"token-%d","token_type":"bearer","expires_in":3600,"scope":"*"}`, n)
	})
	mux.HandleFunc("/r/", f.handleSubreddit)

	f.server = httptest.NewServer(mux)
	t.Cleanup(f.server.Close)
	return f
}

func (f *fakeReddit) handleSubreddit(w http.ResponseWriter, r *http.Request) {
	// Path shapes: /r/{sub}/search?... and /r/{sub}/comments/{id}?...
	parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/r/"), "/")
	if len(parts) < 2 {
		http.Error(w, "bad path", http.StatusBadRequest)
		return
	}
	sub := parts[0]
	w.Header().Set("Content-Type", "application/json")

	if parts[1] == "search" {
		f.handleSearch(w, sub)
		return
	}
	if parts[1] == "comments" && len(parts) >= 3 {
		f.handleComments(w, parts[2])
		return
	}
	http.Error(w, "not found", http.StatusNotFound)
}

func (f *fakeReddit) handleSearch(w http.ResponseWriter, sub string) {
	f.mu.Lock()
	defer f.mu.Unlock()

	switch sub {
	case "ok":
		if !f.search401[sub] {
			f.search401[sub] = true
			w.WriteHeader(http.StatusUnauthorized)
			fmt.Fprint(w, `{"message":"Unauthorized","error":401}`)
			return
		}
		fmt.Fprint(w, `{"data":{"children":[
			{"kind":"t3","data":{"id":"p1","title":"phone battery drains fast","selftext":"my phone battery is terrible","author":"u1","subreddit":"ok","score":10,"num_comments":12,"created_utc":1700000000,"permalink":"/r/ok/comments/p1/x/"}},
			{"kind":"t3","data":{"id":"p2","title":"battery swelling","selftext":"help, battery swelling","author":"u2","subreddit":"ok","score":5,"num_comments":8,"created_utc":1700000100,"permalink":"/r/ok/comments/p2/x/"}},
			{"kind":"t3","data":{"id":"p3","title":"","selftext":"","author":"u3","subreddit":"ok","score":0,"num_comments":0,"created_utc":1700000200,"permalink":"/r/ok/comments/p3/x/"}}
		]}}`)
	case "limited":
		w.WriteHeader(http.StatusTooManyRequests)
		fmt.Fprint(w, `{"message":"Too Many Requests","error":429}`)
	case "badjson":
		fmt.Fprint(w, `this is not json`)
	default:
		fmt.Fprint(w, `{"data":{"children":[]}}`)
	}
}

func (f *fakeReddit) handleComments(w http.ResponseWriter, postID string) {
	f.mu.Lock()
	f.commentCalls[postID]++
	f.mu.Unlock()

	switch postID {
	case "p1":
		fmt.Fprint(w, `[
			{"kind":"Listing","data":{"children":[]}},
			{"kind":"Listing","data":{"children":[
				{"kind":"t1","data":{"id":"c1","body":"same problem here","author":"u9","score":3,"created_utc":1700000500,"parent_id":"t3_p1"}},
				{"kind":"t1","data":{"id":"c2","body":"try a factory reset","author":"u8","score":1,"created_utc":1700000600,"parent_id":"t3_p1"}},
				{"kind":"t1","data":{"id":"c3","body":"[deleted]","author":"u7","score":0,"created_utc":1700000700,"parent_id":"t3_p1"}}
			]}}
		]`)
	case "p2":
		w.WriteHeader(http.StatusInternalServerError)
		fmt.Fprint(w, `{"message":"server error"}`)
	default:
		fmt.Fprint(w, `[{"kind":"Listing","data":{"children":[]}},{"kind":"Listing","data":{"children":[]}}]`)
	}
}

func (f *fakeReddit) authCallCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.authCalls
}

// fakeWriter is a Kafka producer stand-in that records messages in memory.
type fakeWriter struct {
	mu       sync.Mutex
	messages []kafka.Message
}

func (f *fakeWriter) WriteMessages(ctx context.Context, msgs ...kafka.Message) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.messages = append(f.messages, msgs...)
	return nil
}

func (f *fakeWriter) Close() error { return nil }

func (f *fakeWriter) countByType() (posts, comments int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, m := range f.messages {
		var d ScrapedData
		if err := json.Unmarshal(m.Value, &d); err != nil {
			continue
		}
		switch d.DataType {
		case "post":
			posts++
		case "comment":
			comments++
		}
	}
	return
}

func (f *fakeWriter) total() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.messages)
}

func newTestScraper(fake *fakeReddit, writer messageWriter) *RedditScraper {
	cfg := Configuration{
		RedditClientID:     "test-id",
		RedditClientSecret: "test-secret",
		RedditUserAgent:    "test-agent",
		RedditAuthURL:      fake.server.URL + "/api/v1/access_token",
		RedditAPIBaseURL:   fake.server.URL,
		KafkaBootstrap:     "localhost:9092",
		KafkaTopic:         "test-topic",
		RateLimit:          100000,
	}
	s, err := NewRedditScraper(cfg)
	if err != nil {
		panic(err)
	}
	s.writer = writer
	return s
}

func findCombo(t *testing.T, run ScrapeRun, sub, kw string) ComboResult {
	t.Helper()
	for _, r := range run.Results {
		if r.Subreddit == sub && r.Keyword == kw {
			return r
		}
	}
	t.Fatalf("combo %s/%s not found in run results", sub, kw)
	return ComboResult{}
}

func TestSettlementPerCombo(t *testing.T) {
	fake := newFakeReddit(t)
	writer := &fakeWriter{}
	scraper := newTestScraper(fake, writer)
	defer scraper.Close()

	req := ScrapeRequest{
		Product:    "phone",
		Subreddits: []string{"ok", "limited", "badjson"},
		Keywords:   []string{"battery"},
	}

	run := scraper.tracker.createRun(req.Product, req.Subreddits, req.Keywords)
	if err := scraper.ScrapeProduct(run.ID, req); err != nil {
		t.Fatalf("ScrapeProduct: %v", err)
	}

	got, ok := scraper.tracker.get(run.ID)
	if !ok {
		t.Fatal("run not found in tracker")
	}
	if got.Status != "completed" {
		t.Fatalf("run status = %q, want completed", got.Status)
	}
	if got.FinishedAt == nil {
		t.Fatal("finished_at not set on completed run")
	}

	// ok/battery: search 401 -> re-auth -> retry -> success.
	okCombo := findCombo(t, got, "ok", "battery")
	if okCombo.Status != "success" {
		t.Fatalf("ok/battery status = %q (%s), want success", okCombo.Status, okCombo.Error)
	}
	if okCombo.PostsFound != 3 {
		t.Errorf("ok/battery posts_found = %d, want 3", okCombo.PostsFound)
	}
	if okCombo.PostsWritten != 2 {
		t.Errorf("ok/battery posts_written = %d, want 2 (empty post skipped)", okCombo.PostsWritten)
	}
	if okCombo.CommentsWritten != 2 {
		t.Errorf("ok/battery comments_written = %d, want 2 ([deleted] filtered)", okCombo.CommentsWritten)
	}
	// p2's comment endpoint returns 500: recorded, but the post stays written.
	if okCombo.CommentFailures != 1 {
		t.Errorf("ok/battery comment_failures = %d, want 1", okCombo.CommentFailures)
	}

	// limited/battery: 429 -> failed with rate_limited reason.
	limitedCombo := findCombo(t, got, "limited", "battery")
	if limitedCombo.Status != "failed" {
		t.Fatalf("limited/battery status = %q, want failed", limitedCombo.Status)
	}
	if limitedCombo.Reason != ReasonRateLimited {
		t.Errorf("limited/battery reason = %q, want %q", limitedCombo.Reason, ReasonRateLimited)
	}

	// badjson/battery: unparseable body -> failed with search_failed reason.
	badCombo := findCombo(t, got, "badjson", "battery")
	if badCombo.Status != "failed" {
		t.Fatalf("badjson/battery status = %q, want failed", badCombo.Status)
	}
	if badCombo.Reason != ReasonSearchFailed {
		t.Errorf("badjson/battery reason = %q, want %q", badCombo.Reason, ReasonSearchFailed)
	}

	// Totals reflect actual Kafka writes.
	if got.TotalPostsWritten != 2 {
		t.Errorf("total_posts_written = %d, want 2", got.TotalPostsWritten)
	}
	if got.TotalCommentsWritten != 2 {
		t.Errorf("total_comments_written = %d, want 2", got.TotalCommentsWritten)
	}
	posts, comments := writer.countByType()
	if posts != 2 || comments != 2 {
		t.Errorf("kafka writes: %d posts, %d comments; want 2/2", posts, comments)
	}

	// The 401 must have triggered a re-authentication (initial auth + re-auth).
	if n := fake.authCallCount(); n < 2 {
		t.Errorf("auth endpoint called %d times, want >= 2 (re-auth after 401)", n)
	}
}

func TestRerunDoesNotDuplicateKafkaWrites(t *testing.T) {
	fake := newFakeReddit(t)
	writer := &fakeWriter{}
	scraper := newTestScraper(fake, writer)
	defer scraper.Close()

	req := ScrapeRequest{
		Product:    "phone",
		Subreddits: []string{"ok"},
		Keywords:   []string{"battery"},
	}

	// First run: writes 2 posts + 2 comments.
	run1 := scraper.tracker.createRun(req.Product, req.Subreddits, req.Keywords)
	if err := scraper.ScrapeProduct(run1.ID, req); err != nil {
		t.Fatalf("first run: %v", err)
	}
	if total := writer.total(); total != 4 {
		t.Fatalf("after first run: %d kafka messages, want 4", total)
	}

	// Second run for the same product: nothing may be written again.
	run2 := scraper.tracker.createRun(req.Product, req.Subreddits, req.Keywords)
	if err := scraper.ScrapeProduct(run2.ID, req); err != nil {
		t.Fatalf("second run: %v", err)
	}
	if total := writer.total(); total != 4 {
		t.Fatalf("after second run: %d kafka messages, want still 4 (no duplicates)", total)
	}

	got2, _ := scraper.tracker.get(run2.ID)
	combo := findCombo(t, got2, "ok", "battery")
	if combo.PostsWritten != 0 || combo.CommentsWritten != 0 {
		t.Errorf("second run wrote %d posts / %d comments, want 0/0", combo.PostsWritten, combo.CommentsWritten)
	}
	// 2 duplicate posts + 2 duplicate comments skipped.
	if combo.DuplicatesSkipped != 4 {
		t.Errorf("second run duplicates_skipped = %d, want 4", combo.DuplicatesSkipped)
	}
}

func TestHTTPEndpoints(t *testing.T) {
	fake := newFakeReddit(t)
	writer := &fakeWriter{}
	scraper := newTestScraper(fake, writer)
	defer scraper.Close()

	server := httptest.NewServer(NewHTTPServer(scraper).routes())
	defer server.Close()

	// /health stays compatible.
	resp, err := http.Get(server.URL + "/health")
	if err != nil {
		t.Fatalf("GET /health: %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("/health status = %d", resp.StatusCode)
	}

	// /scrape returns immediately with "started" and a run_id.
	resp, err = http.Post(server.URL+"/scrape", "application/json",
		strings.NewReader(`{"product":"phone","subreddits":["ok"],"keywords":["battery"]}`))
	if err != nil {
		t.Fatalf("POST /scrape: %v", err)
	}
	var started struct {
		Status  string `json:"status"`
		Product string `json:"product"`
		RunID   string `json:"run_id"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&started); err != nil {
		t.Fatalf("decode /scrape response: %v", err)
	}
	resp.Body.Close()
	if started.Status != "started" || started.Product != "phone" {
		t.Fatalf("unexpected /scrape response: %+v", started)
	}
	if started.RunID == "" {
		t.Fatal("/scrape response missing run_id")
	}

	// Poll the settlement endpoint until the run reaches its terminal state.
	var final ScrapeRun
	deadline := time.Now().Add(10 * time.Second)
	for {
		resp, err := http.Get(server.URL + "/scrape/status/" + started.RunID)
		if err != nil {
			t.Fatalf("GET /scrape/status/{id}: %v", err)
		}
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("/scrape/status/{id} status = %d", resp.StatusCode)
		}
		if err := json.NewDecoder(resp.Body).Decode(&final); err != nil {
			t.Fatalf("decode run: %v", err)
		}
		resp.Body.Close()
		if final.Status == "completed" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("run stuck in status %q", final.Status)
		}
		time.Sleep(20 * time.Millisecond)
	}

	if final.TotalPostsWritten != 2 || final.TotalCommentsWritten != 2 {
		t.Errorf("run totals = %d posts / %d comments, want 2/2",
			final.TotalPostsWritten, final.TotalCommentsWritten)
	}
	combo := findCombo(t, final, "ok", "battery")
	if combo.Status != "success" || combo.CommentFailures != 1 {
		t.Errorf("combo = %+v, want success with 1 comment failure", combo)
	}

	// The run list endpoint includes the finished run.
	resp, err = http.Get(server.URL + "/scrape/status")
	if err != nil {
		t.Fatalf("GET /scrape/status: %v", err)
	}
	var list struct {
		Runs []ScrapeRun `json:"runs"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&list); err != nil {
		t.Fatalf("decode run list: %v", err)
	}
	resp.Body.Close()
	found := false
	for _, r := range list.Runs {
		if r.ID == started.RunID && r.Status == "completed" {
			found = true
		}
	}
	if !found {
		t.Error("finished run not present in /scrape/status list")
	}

	// Unknown run id -> 404.
	resp, err = http.Get(server.URL + "/scrape/status/does-not-exist")
	if err != nil {
		t.Fatalf("GET unknown run: %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("unknown run status = %d, want 404", resp.StatusCode)
	}
}

func TestScrapeWithDefaultsStillWorks(t *testing.T) {
	fake := newFakeReddit(t)
	writer := &fakeWriter{}
	scraper := newTestScraper(fake, writer)
	defer scraper.Close()

	server := httptest.NewServer(NewHTTPServer(scraper).routes())
	defer server.Close()

	// No subreddits/keywords: handler must apply its defaults and still return started.
	resp, err := http.Post(server.URL+"/scrape", "application/json",
		strings.NewReader(`{"product":"phone"}`))
	if err != nil {
		t.Fatalf("POST /scrape: %v", err)
	}
	var started struct {
		Status string `json:"status"`
		RunID  string `json:"run_id"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&started); err != nil {
		t.Fatalf("decode: %v", err)
	}
	resp.Body.Close()
	if started.Status != "started" || started.RunID == "" {
		t.Fatalf("unexpected response: %+v", started)
	}

	// The run must reach a terminal state covering all 5x7 default combos.
	deadline := time.Now().Add(15 * time.Second)
	for {
		run, ok := scraper.tracker.get(started.RunID)
		if !ok {
			t.Fatal("run not tracked")
		}
		if run.Status == "completed" {
			if len(run.Results) != 5*7 {
				t.Fatalf("completed run has %d combo results, want 35", len(run.Results))
			}
			for _, r := range run.Results {
				if r.Status != "success" {
					t.Fatalf("combo %s/%s = %q, want success (empty fake results)",
						r.Subreddit, r.Keyword, r.Status)
				}
			}
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("run with defaults did not complete in time")
		}
		time.Sleep(50 * time.Millisecond)
	}
}
