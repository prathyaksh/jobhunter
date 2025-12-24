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
#   CONFIGURABLE SECTION (Safe Mode)
# ==============================================================================
CONFIG = {
    # 1. SEARCH SETTINGS
    # We combine locations into one query to save time & API calls
    "locations": ["Hyderabad", "Bangalore", "Pune", "Remote", "India"],
    
    # Synonyms for "SRE" to ensure we catch everything
    "role_queries": ["SRE", "DevOps", "Platform Engineer", "Site Reliability", "Cloud Engineer"],
    "max_results_per_query": 10, 

    # 2. SCORING LOGIC (The "Safe Mode" - No Negatives)
    # CORE SKILLS (+2 Points): The key is the label, the list is the synonyms.
    "core_skills": {
        "GCP": ["gcp", "google cloud", "anthos", "compute engine", "gke", "cloud run", "iam"],
        "Terraform": ["terraform", "iac", "infrastructure as code", "terragrunt"],
        "CI/CD": ["ci/cd", "jenkins", "gitlab ci", "github actions", "pipelines", "argo"],
        "SRE": ["sre", "site reliability", "reliability", "slo", "sli"],
        "DevOps": ["devops", "platform", "cloud engineer"]
    },

    # WARNING SKILLS (0 Points - Just Tagging): 
    # These will NOT lower the score, but will appear in the "Warnings" column.
    "warning_skills": [
        "java developer", "c++", "expert coding", "compiler design", 
        "algorithm expert", "leetcode", "night shift"
    ],
    
    # STALENESS FILTERS (Job rejection keywords)
    # Only reject if we are 100% sure it's closed.
    "stale_keywords": ["job is closed", "position filled", "role is no longer available"]
}

HISTORY_FILE = "job_history.json"

# ==============================================================================
#   MODULE: STATE MANAGEMENT
# ==============================================================================
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_history(history_set):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(list(history_set), f, indent=2)

# ==============================================================================
#   MODULE: SEARCH ENGINE (Updated for Multiple Portals)
# ==============================================================================
def find_jobs():
    # 1. Build the Location String: ("Hyderabad" OR "Bangalore" ...)
    loc_str = " OR ".join([f'"{l}"' for l in CONFIG['locations']])
    location_query = f"({loc_str})"
    
    # 2. Define the Target Sites (All 4 domains)
    sites = [
        "site:boards.greenhouse.io", 
        "site:job-boards.greenhouse.io", 
        "site:jobs.lever.co", 
        "site:jobs.ashbyhq.com"
    ]
    
    print(f"🕵️  Searching for jobs in {CONFIG['locations']}...")
    links = set()
    
    for role in CONFIG["role_queries"]:
        for site in sites:
            # Combined Query: site:greenhouse.io ("Hyderabad" OR "Bangalore") "SRE"
            query = f'{site} {location_query} "{role}"'
            print(f"   -> Querying: {site} + {role}...")
            
            try:
                results = DDGS().text(query, max_results=CONFIG["max_results_per_query"])
                if results:
                    count = 0
                    for r in results:
                        links.add(r['href'])
                        count += 1
                    print(f"      ✅ Found {count} links.")
                else:
                    print(f"      ⚠️ No results found.")
                
                time.sleep(1.5) # Sleep to be polite
            except Exception as e:
                print(f"      ❌ Search Error: {e}")
            
    return list(links)

# ==============================================================================
#   MODULE: ANALYZER (Safe Mode)
# ==============================================================================
def analyze_job(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200: return None

        soup = BeautifulSoup(response.text, 'html.parser')
        for script in soup(["script", "style"]): script.extract()
        text = soup.get_text(" ", strip=True).lower()
        title = soup.title.string if soup.title else "Unknown Role"

        # 1. Staleness Check
        if any(k in text for k in CONFIG["stale_keywords"]):
            return None 

        score = 0
        matches = []
        warnings = []
        missing = []

        # 2. Positive Scoring (+2)
        for label, keywords in CONFIG["core_skills"].items():
            if any(k in text for k in keywords):
                score += 2
                matches.append(label)
            else:
                missing.append(label)

        # 3. Warning Check (0 Points - Just Tagging)
        for bad in CONFIG["warning_skills"]:
            if bad in text:
                warnings.append(bad)

        return {
            "Company": title.split("-")[0].strip() if "-" in title else "Unknown",
            "Job Title": title,
            "Location": "India (Check Link)", # Since we search multiple cities, we generalize
            "Match Score": score,
            "Why? (Matches)": ", ".join(matches),
            "Warnings": ", ".join(warnings),
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

    top_job = job_list[0]
    body = f"""
    Hello!
    
    I found {len(job_list)} potential jobs today.
    
    🏆 TOP MATCH:
    Role: {top_job['Job Title']}
    Score: {top_job['Match Score']}
    Matches: {top_job['Why? (Matches)']}
    Warnings: {top_job['Warnings'] if top_job['Warnings'] else "None"}
    
    Full list attached.
    """
    msg.attach(MIMEText(body, 'plain'))

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
#   MAIN
# ==============================================================================
def main():
    print("🤖 JobBot Started (Safe Mode)...")
    history = load_history()
    raw_links = find_jobs()
    
    new_links = [link for link in raw_links if link not in history]
    print(f"🔎 Found {len(raw_links)} total links. {len(new_links)} are new.")

    if not new_links:
        print("💤 No new jobs to analyze.")
        return

    analyzed_jobs = []
    print("📝 Analyzing contents...")
    
    for link in new_links:
        data = analyze_job(link)
        history.add(link) 
        
        # In Safe Mode, we keep EVERYTHING with a score > 0
        if data and data['Match Score'] > 0:
            analyzed_jobs.append(data)
        
        time.sleep(1)

    if analyzed_jobs:
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
        print("❌ No matching jobs found.")

    save_history(history)

if __name__ == "__main__":
    main()