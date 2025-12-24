import os
import csv
import json
import smtplib
import time
import random
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from datetime import datetime
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

# ==============================================================================
#   CONFIGURABLE SECTION (The User Interface)
# ==============================================================================
CONFIG = {
    # 1. SEARCH SETTINGS
    "location": ["Hyderabad", "Banglore", "mumbai", "pune"],
    # Synonyms for "SRE" to ensure we catch everything
    "role_queries": ["SRE", "DevOps", "Platform Engineer", "Site Reliability"],
    "max_results_per_query": 10, 

    # 2. SCORING LOGIC (The "Fit Finder")
    # CORE SKILLS (+2 Points): The key is the label, the list is the synonyms.
    "core_skills": {
        "GCP": ["gcp", "google cloud", "cloud sql", "cloud storage", "cloud composer", "cloud run", "Load Balancer", "IAM"],
        "Terraform": ["terraform", "iac", "infrastructure as code", "terragrunt"],
        "CI/CD": ["ci/cd", "jenkins", "gitlab ci", "github actions", "pipelines", "argo"],
        "SRE": ["sre", "site reliability", "reliability", "slo", "sli"],
        "DevOps": ["devops", "platform", "cloud engineer"]
    },

    # NEUTRAL SKILLS (0 Points): Safe to have.
    "neutral_skills": [
        "python", "bash", "shell", "scripting", "docker", "containers", 
        "monitoring", "datadog", "prometheus", "grafana", "linux", 
        "kubernetes", "k8s", "ansible" 
    ],

    # AVOID SKILLS (-5 Points): The "Red Flags".
    "avoid_skills": [
        "java developer", "c++", "expert coding", "compiler design", 
        "algorithm expert", "leetcode"
    ],
    
    # STALENESS FILTERS (Job rejection keywords)
    "stale_keywords": ["job is closed", "position filled", "role is no longer available", "archive"]
}

HISTORY_FILE = "job_history.json"

# ==============================================================================
#   MODULE: STATE MANAGEMENT
# ==============================================================================
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                return set(json.load(f)) # Use set for fast lookup
        except:
            return set()
    return set()

def save_history(history_set):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(list(history_set), f, indent=2)

# ==============================================================================
#   MODULE: SEARCH ENGINE (DuckDuckGo)
# ==============================================================================
def find_jobs():
    print(f"🕵️  Searching for jobs in {CONFIG['location']}...")
    links = set()
    
    for role in CONFIG["role_queries"]:
        # Search mainly for Greenhouse and Lever as they are easy to parse
        query = f'site:boards.greenhouse.io OR site:jobs.lever.co "{CONFIG["location"]}" "{role}"'
        try:
            results = DDGS().text(query, max_results=CONFIG["max_results_per_query"])
            if results:
                for r in results:
                    links.add(r['href'])
            time.sleep(1) # Be polite to the search engine
        except Exception as e:
            print(f"⚠️ Search warning for {role}: {e}")
            
    return list(links)

# ==============================================================================
#   MODULE: ANALYZER (Weighted Scoring)
# ==============================================================================
def analyze_job(url):
    try:
        # User-Agent to look like a real browser
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0'}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200: return None

        soup = BeautifulSoup(response.text, 'html.parser')
        # Get text and clean it
        for script in soup(["script", "style"]): script.extract()
        text = soup.get_text(" ", strip=True).lower()
        title = soup.title.string if soup.title else "Unknown Role"

        # 1. Staleness Check
        if any(k in text for k in CONFIG["stale_keywords"]):
            return None # Job is closed

        # 2. Scoring
        score = 0
        matches = []
        red_flags = []
        missing = []

        # Check Core Skills (+2)
        for label, keywords in CONFIG["core_skills"].items():
            if any(k in text for k in keywords):
                score += 2
                matches.append(label)
            else:
                missing.append(label)

        # Check Avoid Skills (-5) - Safety check if list is empty
        if CONFIG["avoid_skills"]:
            for bad in CONFIG["avoid_skills"]:
                if bad in text:
                    score -= 5
                    red_flags.append(bad)

        return {
            "Company": title.split("-")[0].strip() if "-" in title else "Unknown",
            "Job Title": title,
            "Location": CONFIG["location"],
            "Match Score": score,
            "Why? (Matches)": ", ".join(matches),
            "Red Flags": ", ".join(red_flags),
            "Missing Keywords": ", ".join(missing),
            "URL": url
        }
    except Exception:
        return None

# ==============================================================================
#   MODULE: EMAILER
# ==============================================================================
def send_email(job_list, filename):
    sender = os.environ.get("EMAIL_SENDER")
    password = os.environ.get("EMAIL_PASSWORD")
    recipient = os.environ.get("EMAIL_RECIPIENT")

    if not sender or not password:
        print("⚠️ Email credentials missing. Skipping email.")
        return

    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = recipient
    msg['Subject'] = f"🚀 JobBot: {len(job_list)} New Matches ({datetime.now().strftime('%Y-%m-%d')})"

    # Email Body
    top_job = job_list[0]
    body = f"""
    Hello!
    
    I found {len(job_list)} new job openings in {CONFIG['location']} today.
    
    🏆 TOP MATCH:
    Role: {top_job['Job Title']}
    Score: {top_job['Match Score']}
    Matches: {top_job['Why? (Matches)']}
    
    The full list is attached as a CSV file.
    
    Happy Hunting!
    - JobBot
    """
    msg.attach(MIMEText(body, 'plain'))

    # Attach CSV
    with open(filename, "rb") as f:
        part = MIMEApplication(f.read(), Name=filename)
        part['Content-Disposition'] = f'attachment; filename="{filename}"'
        msg.attach(part)

    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
        print("✅ Email sent successfully!")
    except Exception as e:
        print(f"❌ Email Failed: {e}")

# ==============================================================================
#   MAIN EXECUTION
# ==============================================================================
def main():
    print("🤖 JobBot Started...")
    
    # Load History
    history = load_history()
    
    # Find Jobs
    raw_links = find_jobs()
    
    # Deduplicate (Ignore jobs we've already seen)
    new_links = [link for link in raw_links if link not in history]
    print(f"🔎 Found {len(raw_links)} total links. {len(new_links)} are new.")

    if not new_links:
        print("💤 No new jobs to analyze.")
        return

    analyzed_jobs = []
    
    # Analyze
    print("📝 Analyzing contents...")
    for link in new_links:
        data = analyze_job(link)
        
        # We add to history regardless of score so we don't re-scan bad jobs
        history.add(link) 
        
        # Only keep jobs with positive score
        if data and data['Match Score'] > 0:
            analyzed_jobs.append(data)
        
        time.sleep(random.uniform(0.5, 1.5)) # Random delay

    # Report & Save
    if analyzed_jobs:
        # Sort High to Low
        analyzed_jobs.sort(key=lambda x: x["Match Score"], reverse=True)
        
        filename = f"jobs_{datetime.now().strftime('%Y%m%d')}.csv"
        keys = analyzed_jobs[0].keys()
        
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(analyzed_jobs)
            
        print(f"✅ Generated report: {filename}")
        send_email(analyzed_jobs, filename)
    else:
        print("❌ New jobs were found, but none matched your requirements.")

    # Save State
    save_history(history)

if __name__ == "__main__":
    main()