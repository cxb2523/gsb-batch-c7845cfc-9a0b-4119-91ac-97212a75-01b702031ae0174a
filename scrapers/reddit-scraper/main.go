package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/joho/godotenv"
	"github.com/segmentio/kafka-go"
	log "github.com/sirupsen/logrus"
	"golang.org/x/time/rate"
)

const maxWorkers = 5 // <-- you asked for 5 workers

// Failure reason categories recorded in the per-combo settlement results.
const (
	ReasonRateLimited   = "rate_limited"
	ReasonAuthFailed    = "auth_failed"
	ReasonSearchFailed  = "search_failed"
	ReasonHTTPError     = "http_error"
	ReasonRequestFailed = "request_failed"
)

// Configuration holds all scraper settings
type Configuration struct {
	RedditClientID     string
	RedditClientSecret string
	RedditUserAgent    string
	RedditAuthURL      string
	RedditAPIBaseURL   string
	KafkaBootstrap     string
	KafkaTopic         string
	RateLimit          int
	ScrapeInterval     time.Duration
}

// RedditAuth represents OAuth token response
type RedditAuth struct {
	AccessToken string `json:"access_token"`
	TokenType   string `json:"token_type"`
	ExpiresIn   int    `json:"expires_in"`
	Scope       string `json:"scope"`
}

// RedditPost represents a single Reddit post
type RedditPost struct {
	ID          string  `json:"id"`
	Title       string  `json:"title"`
	Selftext    string  `json:"selftext"`
	Author      string  `json:"author"`
	Subreddit   string  `json:"subreddit"`
	Score       int     `json:"score"`
	NumComments int     `json:"num_comments"`
	CreatedUTC  float64 `json:"created_utc"`
	URL         string  `json:"url"`
	Permalink   string  `json:"permalink"`
}

// RedditComment represents a Reddit comment
type RedditComment struct {
	ID         string  `json:"id"`
	Body       string  `json:"body"`
	Author     string  `json:"author"`
	Score      int     `json:"score"`
	CreatedUTC float64 `json:"created_utc"`
	ParentID   string  `json:"parent_id"`
	PostID     string  `json:"post_id"`
}

// ScrapedData represents data to be sent to Kafka
type ScrapedData struct {
	Source    string            `json:"source"`
	Product   string            `json:"product"`
	DataType  string            `json:"data_type"`
	Content   string            `json:"content"`
	Title     string            `json:"title,omitempty"`
	Author    string            `json:"author"`
	Score     int               `json:"score"`
	URL       string            `json:"url"`
	Subreddit string            `json:"subreddit,omitempty"`
	CreatedAt time.Time         `json:"created_at"`
	ScrapedAt time.Time         `json:"scraped_at"`
	Metadata  map[string]string `json:"metadata"`
}

// ScrapeRequest represents a product scrape request from API
type ScrapeRequest struct {
	Product    string   `json:"product"`
	Subreddits []string `json:"subreddits"`
	Keywords   []string `json:"keywords"`
}

// Task used by worker pool
type Task struct {
	Product   string
	Subreddit string
	Keyword   string
}

// scrapeError classifies a failure so it can be recorded with a stable reason.
type scrapeError struct {
	reason    string
	retryable bool // true only for 401 responses, which get exactly one retry
	err       error
}

func (e *scrapeError) Error() string { return e.err.Error() }
func (e *scrapeError) Unwrap() error { return e.err }

// reasonOf extracts the stable failure category from an error.
func reasonOf(err error) string {
	var se *scrapeError
	if errors.As(err, &se) {
		return se.reason
	}
	return ReasonRequestFailed
}

// ComboResult is the settlement record for one subreddit+keyword combination.
type ComboResult struct {
	Subreddit         string `json:"subreddit"`
	Keyword           string `json:"keyword"`
	Status            string `json:"status"` // "running" | "success" | "failed"
	Reason            string `json:"reason,omitempty"`
	Error             string `json:"error,omitempty"`
	PostsFound        int    `json:"posts_found"`
	PostsWritten      int    `json:"posts_written"`
	PostsFailed       int    `json:"posts_failed"`
	CommentsWritten   int    `json:"comments_written"`
	CommentFailures   int    `json:"comment_failures"`
	DuplicatesSkipped int    `json:"duplicates_skipped"`
}

// ScrapeRun is the settlement record for one /scrape request.
type ScrapeRun struct {
	ID                   string        `json:"run_id"`
	Product              string        `json:"product"`
	Status               string        `json:"status"` // "running" | "completed"
	StartedAt            time.Time     `json:"started_at"`
	FinishedAt           *time.Time    `json:"finished_at,omitempty"`
	Results              []ComboResult `json:"results"`
	TotalPostsWritten    int           `json:"total_posts_written"`
	TotalCommentsWritten int           `json:"total_comments_written"`
}

// RunTracker keeps the settlement state of all scrape runs.
type RunTracker struct {
	mu    sync.RWMutex
	runs  map[string]*ScrapeRun
	order []string
}

func newRunTracker() *RunTracker {
	return &RunTracker{runs: make(map[string]*ScrapeRun)}
}

func newRunID() string {
	b := make([]byte, 8)
	if _, err := rand.Read(b); err != nil {
		return fmt.Sprintf("run-%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(b)
}

// createRun registers a new run with one pending entry per subreddit+keyword combo.
func (t *RunTracker) createRun(product string, subreddits, keywords []string) *ScrapeRun {
	run := &ScrapeRun{
		ID:        newRunID(),
		Product:   product,
		Status:    "running",
		StartedAt: time.Now(),
	}
	for _, sub := range subreddits {
		for _, kw := range keywords {
			run.Results = append(run.Results, ComboResult{
				Subreddit: sub,
				Keyword:   kw,
				Status:    "running",
			})
		}
	}

	t.mu.Lock()
	t.runs[run.ID] = run
	t.order = append(t.order, run.ID)
	t.mu.Unlock()
	return run
}

func (t *RunTracker) completeCombo(runID string, res ComboResult) {
	t.mu.Lock()
	defer t.mu.Unlock()
	run, ok := t.runs[runID]
	if !ok {
		return
	}
	for i := range run.Results {
		if run.Results[i].Subreddit == res.Subreddit && run.Results[i].Keyword == res.Keyword {
			run.Results[i] = res
			return
		}
	}
	run.Results = append(run.Results, res)
}

func (t *RunTracker) finishRun(runID string) {
	t.mu.Lock()
	defer t.mu.Unlock()
	run, ok := t.runs[runID]
	if !ok {
		return
	}
	now := time.Now()
	run.Status = "completed"
	run.FinishedAt = &now
	run.TotalPostsWritten = 0
	run.TotalCommentsWritten = 0
	for _, r := range run.Results {
		run.TotalPostsWritten += r.PostsWritten
		run.TotalCommentsWritten += r.CommentsWritten
	}
}

// get returns a copy of the run so callers never observe partial mutation.
func (t *RunTracker) get(runID string) (ScrapeRun, bool) {
	t.mu.RLock()
	defer t.mu.RUnlock()
	run, ok := t.runs[runID]
	if !ok {
		return ScrapeRun{}, false
	}
	cp := *run
	cp.Results = append([]ComboResult(nil), run.Results...)
	return cp, true
}

// list returns copies of all runs in creation order.
func (t *RunTracker) list() []ScrapeRun {
	t.mu.RLock()
	defer t.mu.RUnlock()
	out := make([]ScrapeRun, 0, len(t.order))
	for _, id := range t.order {
		run := t.runs[id]
		cp := *run
		cp.Results = append([]ComboResult(nil), run.Results...)
		out = append(out, cp)
	}
	return out
}

// messageWriter abstracts the Kafka producer so tests can substitute a fake.
type messageWriter interface {
	WriteMessages(ctx context.Context, msgs ...kafka.Message) error
	Close() error
}

// RedditScraper handles Reddit API interactions
type RedditScraper struct {
	config      Configuration
	accessToken string
	tokenExpiry time.Time
	httpClient  *http.Client
	writer      messageWriter
	limiter     *rate.Limiter
	tracker     *RunTracker
	mu          sync.RWMutex
	seenMu      sync.Mutex
	seen        map[string]struct{} // dedup keys for posts/comments already written to Kafka
}

func NewRedditScraper(config Configuration) (*RedditScraper, error) {
	writer := &kafka.Writer{
		Addr:         kafka.TCP(config.KafkaBootstrap),
		Topic:        config.KafkaTopic,
		Balancer:     &kafka.LeastBytes{},
		BatchTimeout: 10 * time.Millisecond,
		RequiredAcks: kafka.RequireAll,
		Async:        false,
	}

	limiter := rate.NewLimiter(rate.Limit(float64(config.RateLimit)/60.0), config.RateLimit/10)

	return &RedditScraper{
		config:     config,
		httpClient: &http.Client{Timeout: 30 * time.Second},
		writer:     writer,
		limiter:    limiter,
		tracker:    newRunTracker(),
		seen:       make(map[string]struct{}),
	}, nil
}

func (s *RedditScraper) authenticate() error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.accessToken != "" && time.Now().Before(s.tokenExpiry) {
		return nil
	}

	data := url.Values{}
	data.Set("grant_type", "client_credentials")

	req, err := http.NewRequest("POST", s.config.RedditAuthURL, strings.NewReader(data.Encode()))
	if err != nil {
		return fmt.Errorf("failed to create auth request: %w", err)
	}

	req.SetBasicAuth(s.config.RedditClientID, s.config.RedditClientSecret)
	req.Header.Set("User-Agent", s.config.RedditUserAgent)
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	resp, err := s.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("auth request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("auth failed with status %d: %s", resp.StatusCode, string(body))
	}

	var auth RedditAuth
	if err := json.NewDecoder(resp.Body).Decode(&auth); err != nil {
		return fmt.Errorf("failed to decode auth response: %w", err)
	}

	s.accessToken = auth.AccessToken
	// subtract a minute as buffer
	s.tokenExpiry = time.Now().Add(time.Duration(auth.ExpiresIn-60) * time.Second)

	log.Info("Successfully authenticated with Reddit API")
	return nil
}

// doRequest performs a single HTTP attempt against the Reddit API.
func (s *RedditScraper) doRequest(endpoint string) ([]byte, *scrapeError) {
	ctx := context.Background()

	// Rate limiter wait
	if err := s.limiter.Wait(ctx); err != nil {
		return nil, &scrapeError{reason: ReasonRequestFailed, err: fmt.Errorf("rate limiter error: %w", err)}
	}

	if err := s.authenticate(); err != nil {
		return nil, &scrapeError{reason: ReasonAuthFailed, err: err}
	}

	s.mu.RLock()
	token := s.accessToken
	s.mu.RUnlock()

	req, err := http.NewRequest("GET", s.config.RedditAPIBaseURL+endpoint, nil)
	if err != nil {
		return nil, &scrapeError{reason: ReasonRequestFailed, err: fmt.Errorf("failed to create request: %w", err)}
	}

	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("User-Agent", s.config.RedditUserAgent)

	resp, err := s.httpClient.Do(req)
	if err != nil {
		return nil, &scrapeError{reason: ReasonRequestFailed, err: fmt.Errorf("request failed: %w", err)}
	}
	defer resp.Body.Close()

	switch resp.StatusCode {
	case http.StatusOK:
		body, err := io.ReadAll(resp.Body)
		if err != nil {
			return nil, &scrapeError{reason: ReasonRequestFailed, err: fmt.Errorf("failed to read response: %w", err)}
		}
		return body, nil
	case http.StatusUnauthorized:
		return nil, &scrapeError{reason: ReasonAuthFailed, retryable: true, err: fmt.Errorf("request unauthorized (401)")}
	case http.StatusTooManyRequests:
		return nil, &scrapeError{reason: ReasonRateLimited, err: fmt.Errorf("rate limited by Reddit (429)")}
	default:
		body, _ := io.ReadAll(resp.Body)
		return nil, &scrapeError{reason: ReasonHTTPError, err: fmt.Errorf("request failed with status %d: %s", resp.StatusCode, string(body))}
	}
}

// makeRequest performs the request, and on a 401 re-authenticates and retries exactly once.
func (s *RedditScraper) makeRequest(endpoint string) ([]byte, error) {
	body, serr := s.doRequest(endpoint)
	if serr == nil {
		return body, nil
	}
	if !serr.retryable {
		return nil, serr
	}

	// 401: drop the token so the retry re-authenticates, then try exactly once more.
	log.WithError(serr).Warn("Request unauthorized, re-authenticating and retrying once")
	s.mu.Lock()
	s.accessToken = ""
	s.mu.Unlock()

	body, serr = s.doRequest(endpoint)
	if serr != nil {
		return nil, serr
	}
	return body, nil
}

func (s *RedditScraper) searchPosts(query string, subreddit string, limit int) ([]RedditPost, error) {
	endpoint := fmt.Sprintf("/r/%s/search?q=%s&limit=%d&sort=new&restrict_sr=on&type=link",
		subreddit, url.QueryEscape(query), limit)

	body, err := s.makeRequest(endpoint)
	if err != nil {
		return nil, err
	}

	var result struct {
		Data struct {
			Children []struct {
				Data RedditPost `json:"data"`
			} `json:"children"`
		} `json:"data"`
	}

	if err := json.Unmarshal(body, &result); err != nil {
		return nil, &scrapeError{reason: ReasonSearchFailed, err: fmt.Errorf("failed to parse response: %w", err)}
	}

	posts := make([]RedditPost, len(result.Data.Children))
	for i, child := range result.Data.Children {
		posts[i] = child.Data
	}

	return posts, nil
}

func (s *RedditScraper) getComments(postID string, subreddit string, limit int) ([]RedditComment, error) {
	endpoint := fmt.Sprintf("/r/%s/comments/%s?limit=%d&sort=best&depth=2",
		subreddit, postID, limit)

	body, err := s.makeRequest(endpoint)
	if err != nil {
		return nil, err
	}

	var result []json.RawMessage
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	if len(result) < 2 {
		return nil, nil
	}

	var commentsData struct {
		Data struct {
			Children []struct {
				Kind string `json:"kind"`
				Data struct {
					ID         string  `json:"id"`
					Body       string  `json:"body"`
					Author     string  `json:"author"`
					Score      int     `json:"score"`
					CreatedUTC float64 `json:"created_utc"`
					ParentID   string  `json:"parent_id"`
				} `json:"data"`
			} `json:"children"`
		} `json:"data"`
	}

	if err := json.Unmarshal(result[1], &commentsData); err != nil {
		return nil, fmt.Errorf("failed to parse comments: %w", err)
	}

	comments := make([]RedditComment, 0)
	for _, child := range commentsData.Data.Children {
		if child.Kind == "t1" && child.Data.Body != "" && child.Data.Body != "[deleted]" {
			comments = append(comments, RedditComment{
				ID:         child.Data.ID,
				Body:       child.Data.Body,
				Author:     child.Data.Author,
				Score:      child.Data.Score,
				CreatedUTC: child.Data.CreatedUTC,
				ParentID:   child.Data.ParentID,
				PostID:     postID,
			})
		}
	}

	return comments, nil
}

func (s *RedditScraper) sendToKafka(data ScrapedData) error {
	jsonData, err := json.Marshal(data)
	if err != nil {
		return fmt.Errorf("failed to marshal data: %w", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	err = s.writer.WriteMessages(ctx, kafka.Message{
		Key:   []byte(data.Product),
		Value: jsonData,
		Time:  time.Now(),
	})

	if err != nil {
		return fmt.Errorf("failed to write message to Kafka: %w", err)
	}

	return nil
}

// markPending records a dedup key. It returns true if the key is new (caller
// must write it), false if it was already written before.
func (s *RedditScraper) markPending(key string) bool {
	s.seenMu.Lock()
	defer s.seenMu.Unlock()
	if _, ok := s.seen[key]; ok {
		return false
	}
	s.seen[key] = struct{}{}
	return true
}

// unmark releases a dedup key after a failed write so a later run may retry it.
func (s *RedditScraper) unmark(key string) {
	s.seenMu.Lock()
	defer s.seenMu.Unlock()
	delete(s.seen, key)
}

// postStats is what processPost contributes to the combo settlement.
type postStats struct {
	postWritten      bool
	postFailed       bool
	postDuplicate    bool
	commentsWritten  int
	commentFailures  int
	commentDuplicate int
}

func (s *RedditScraper) processPost(product, keyword, subreddit string, post RedditPost) postStats {
	var stats postStats

	// Skip empty posts
	if post.Title == "" && post.Selftext == "" {
		return stats
	}

	content := post.Selftext
	if content == "" {
		content = post.Title
	}

	postKey := product + "|post|" + post.ID
	if s.markPending(postKey) {
		scrapedPost := ScrapedData{
			Source:    "reddit",
			Product:   product,
			DataType:  "post",
			Content:   content,
			Title:     post.Title,
			Author:    post.Author,
			Score:     post.Score,
			URL:       "https://reddit.com" + post.Permalink,
			Subreddit: subreddit,
			CreatedAt: time.Unix(int64(post.CreatedUTC), 0),
			ScrapedAt: time.Now(),
			Metadata: map[string]string{
				"post_id":      post.ID,
				"num_comments": fmt.Sprintf("%d", post.NumComments),
				"keyword":      keyword,
			},
		}

		if err := s.sendToKafka(scrapedPost); err != nil {
			s.unmark(postKey) // not written: allow a later run to retry it
			stats.postFailed = true
			log.WithError(err).Warn("Failed to send post to Kafka")
		} else {
			stats.postWritten = true
		}
	} else {
		stats.postDuplicate = true
	}

	// Fetch comments (sequentially here to avoid lots of concurrent comment fetches).
	// A comment failure is recorded but never affects the already-sent post.
	if post.NumComments > 5 {
		comments, err := s.getComments(post.ID, subreddit, 20)
		if err != nil {
			stats.commentFailures++
			log.WithError(err).WithField("post_id", post.ID).Warn("Failed to fetch comments")
		} else {
			for _, comment := range comments {
				commentKey := product + "|comment|" + comment.ID
				if !s.markPending(commentKey) {
					stats.commentDuplicate++
					continue
				}
				scrapedComment := ScrapedData{
					Source:    "reddit",
					Product:   product,
					DataType:  "comment",
					Content:   comment.Body,
					Author:    comment.Author,
					Score:     comment.Score,
					URL:       "https://reddit.com" + post.Permalink,
					Subreddit: subreddit,
					CreatedAt: time.Unix(int64(comment.CreatedUTC), 0),
					ScrapedAt: time.Now(),
					Metadata: map[string]string{
						"comment_id": comment.ID,
						"post_id":    post.ID,
						"parent_id":  comment.ParentID,
					},
				}
				if err := s.sendToKafka(scrapedComment); err != nil {
					s.unmark(commentKey) // not written: allow a later run to retry it
					stats.commentFailures++
					log.WithError(err).Warn("Failed to send comment to Kafka")
				} else {
					stats.commentsWritten++
				}
			}
		}
	}

	return stats
}

// scrapeKeywordForSubreddit handles the work for a single subreddit+keyword combo
// and returns its settlement record.
func (s *RedditScraper) scrapeKeywordForSubreddit(product, subreddit, keyword string) ComboResult {
	result := ComboResult{
		Subreddit: subreddit,
		Keyword:   keyword,
	}

	searchQuery := fmt.Sprintf("\"%s\" AND %s", product, keyword)

	posts, err := s.searchPosts(searchQuery, subreddit, 25)
	if err != nil {
		result.Status = "failed"
		result.Reason = reasonOf(err)
		result.Error = err.Error()
		log.WithError(err).WithFields(log.Fields{
			"subreddit": subreddit,
			"keyword":   keyword,
			"reason":    result.Reason,
		}).Warn("Search failed")
		return result
	}

	result.Status = "success"
	result.PostsFound = len(posts)

	// Small semaphore to limit how many posts we process concurrently per task.
	// This prevents a single task from spawning too many goroutines.
	const perTaskConcurrency = 3
	sem := make(chan struct{}, perTaskConcurrency)

	var wg sync.WaitGroup
	var mu sync.Mutex
	for _, p := range posts {
		post := p
		sem <- struct{}{}
		wg.Add(1)

		go func() {
			defer wg.Done()
			defer func() { <-sem }()
			stats := s.processPost(product, keyword, subreddit, post)

			mu.Lock()
			if stats.postWritten {
				result.PostsWritten++
			}
			if stats.postFailed {
				result.PostsFailed++
			}
			if stats.postDuplicate {
				result.DuplicatesSkipped++
			}
			result.CommentsWritten += stats.commentsWritten
			result.CommentFailures += stats.commentFailures
			result.DuplicatesSkipped += stats.commentDuplicate
			mu.Unlock()
		}()
	}

	wg.Wait()
	return result
}

// ScrapeProduct enqueues tasks (subreddit × keyword) and processes them with a worker pool,
// recording the settlement of each combo into the run tracker.
func (s *RedditScraper) ScrapeProduct(runID string, request ScrapeRequest) error {
	log.WithFields(log.Fields{
		"run_id":     runID,
		"product":    request.Product,
		"subreddits": request.Subreddits,
		"keywords":   request.Keywords,
	}).Info("Starting parallel scrape (worker pool)")

	taskCount := len(request.Subreddits) * len(request.Keywords)
	taskChan := make(chan Task, taskCount)

	var wg sync.WaitGroup

	// Start workers
	for i := 0; i < maxWorkers; i++ {
		wg.Add(1)
		go func(workerID int) {
			defer wg.Done()
			for task := range taskChan {
				log.WithFields(log.Fields{
					"worker":    workerID,
					"run_id":    runID,
					"subreddit": task.Subreddit,
					"keyword":   task.Keyword,
				}).Info("Worker picked up task")
				result := s.scrapeKeywordForSubreddit(task.Product, task.Subreddit, task.Keyword)
				s.tracker.completeCombo(runID, result)
			}
		}(i)
	}

	// Enqueue tasks
	for _, sub := range request.Subreddits {
		for _, kw := range request.Keywords {
			taskChan <- Task{
				Product:   request.Product,
				Subreddit: sub,
				Keyword:   kw,
			}
		}
	}

	close(taskChan) // signal workers no more tasks
	wg.Wait()       // wait for all workers to finish

	s.tracker.finishRun(runID)

	log.WithFields(log.Fields{
		"run_id":  runID,
		"product": request.Product,
	}).Info("Completed parallel scrape")

	return nil
}

func (s *RedditScraper) Close() {
	if s.writer != nil {
		s.writer.Close()
	}
}

// HTTP Server for receiving scrape requests
type HTTPServer struct {
	scraper *RedditScraper
}

func NewHTTPServer(scraper *RedditScraper) *HTTPServer {
	return &HTTPServer{scraper: scraper}
}

func (h *HTTPServer) handleScrape(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var request ScrapeRequest
	if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
		http.Error(w, "Invalid request body", http.StatusBadRequest)
		return
	}

	// Default subreddits if none provided
	if len(request.Subreddits) == 0 {
		request.Subreddits = []string{"technology", "gadgets", "samsung", "Android", "samsunggalaxy"}
	}

	// Default keywords if none provided
	if len(request.Keywords) == 0 {
		request.Keywords = []string{"review", "problem", "issue", "help", "question", "experience", "opinion"}
	}

	run := h.scraper.tracker.createRun(request.Product, request.Subreddits, request.Keywords)

	// Run scrape in background (one goroutine per HTTP request)
	go func() {
		if err := h.scraper.ScrapeProduct(run.ID, request); err != nil {
			log.WithError(err).Error("Scrape failed")
		}
	}()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"status":  "started",
		"product": request.Product,
		"run_id":  run.ID,
	})
}

// handleScrapeStatus serves the settlement results:
// GET /scrape/status          -> all runs
// GET /scrape/status/{run_id} -> one run with per-combo details
func (h *HTTPServer) handleScrapeStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	runID := strings.Trim(strings.TrimPrefix(r.URL.Path, "/scrape/status"), "/")

	w.Header().Set("Content-Type", "application/json")

	if runID == "" {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"runs": h.scraper.tracker.list(),
		})
		return
	}

	run, ok := h.scraper.tracker.get(runID)
	if !ok {
		http.Error(w, `{"error":"run not found"}`, http.StatusNotFound)
		return
	}
	json.NewEncoder(w).Encode(run)
}

func (h *HTTPServer) handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "healthy"})
}

// routes wires all HTTP handlers so tests can reuse the same mux.
func (h *HTTPServer) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/scrape", h.handleScrape)
	mux.HandleFunc("/scrape/status", h.handleScrapeStatus)
	mux.HandleFunc("/scrape/status/", h.handleScrapeStatus)
	mux.HandleFunc("/health", h.handleHealth)
	return mux
}

func loadConfig() Configuration {
	// Try to load .env file
	_ = godotenv.Load()

	rateLimit := 100
	if rl := os.Getenv("SCRAPE_RATE_LIMIT"); rl != "" {
		fmt.Sscanf(rl, "%d", &rateLimit)
	}

	interval := 60
	if si := os.Getenv("SCRAPE_INTERVAL_SECONDS"); si != "" {
		fmt.Sscanf(si, "%d", &interval)
	}

	return Configuration{
		RedditClientID:     os.Getenv("REDDIT_CLIENT_ID"),
		RedditClientSecret: os.Getenv("REDDIT_CLIENT_SECRET"),
		RedditUserAgent:    os.Getenv("REDDIT_USER_AGENT"),
		RedditAuthURL:      getEnvOrDefault("REDDIT_AUTH_URL", "https://www.reddit.com/api/v1/access_token"),
		RedditAPIBaseURL:   getEnvOrDefault("REDDIT_API_BASE_URL", "https://oauth.reddit.com"),
		KafkaBootstrap:     getEnvOrDefault("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092"),
		KafkaTopic:         getEnvOrDefault("KAFKA_TOPIC_RAW_DATA", "raw_social_data"),
		RateLimit:          rateLimit,
		ScrapeInterval:     time.Duration(interval) * time.Second,
	}
}

func getEnvOrDefault(key, defaultValue string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultValue
}

func main() {
	log.SetFormatter(&log.JSONFormatter{})
	log.SetLevel(log.InfoLevel)

	config := loadConfig()

	if config.RedditClientID == "" || config.RedditClientSecret == "" {
		log.Fatal("Reddit credentials not configured")
	}

	scraper, err := NewRedditScraper(config)
	if err != nil {
		log.WithError(err).Fatal("Failed to create scraper")
	}
	defer scraper.Close()

	server := NewHTTPServer(scraper)

	// Graceful shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		log.Info("Starting Reddit scraper server on :8081")
		if err := http.ListenAndServe(":8081", server.routes()); err != nil {
			log.WithError(err).Fatal("Server failed")
		}
	}()

	<-sigChan
	log.Info("Shutting down gracefully...")
}
