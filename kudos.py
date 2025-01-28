import json
from datetime import datetime, timezone, timedelta
import os, sys
import shutil
import requests
import shutil
from pathlib import Path
import os
import re
import requests
import subprocess


from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Configuration
AUTH_URL = (
    "https://raven.cam.ac.uk/auth/authenticate.html"
    "?ver=3&url=https%3A%2F%2Fkudos.chu.cam.ac.uk%2Flogin"
    "&desc=The%20KuDoS%20Project&msg=you%20requested%20an%20interactive%20login"
)
TARGET_DOMAIN = "kudos.chu.cam.ac.uk"

def login():
    # Set up Selenium WebDriver
    options = Options()

    print("Starting browser automation...")
    with webdriver.Chrome(options=options) as driver:
        # Open the authentication URL
        driver.get(AUTH_URL)
        print("Waiting for user to log in...")

        # Wait for the redirect to the target domain
        WebDriverWait(driver, 300).until(EC.url_contains(TARGET_DOMAIN))

        print("Redirected to target domain. Extracting username and cookies...")
        username = ""

        # Wait for the username element to appear
        try:
            username_element = WebDriverWait(driver, 300).until(
                EC.presence_of_element_located((By.XPATH, "/html/body/kudos/kloginoptions/div/div/div[1]/div/div[2]"))
            )
            username = username_element.text.replace("Username: ", "").strip()
            print(f"Extracted username: {username}")
        except Exception as e:
            print(f"Failed to extract username: {e}")

        # Get the `KuDoSAuth` cookie
        cookies = driver.get_cookies()
        kudos_auth_cookie = next(
            (cookie for cookie in cookies if cookie["name"] == "KuDoSAuth"), None
        )

        if kudos_auth_cookie:
            print("KuDoSAuth cookie extracted successfully!")
            print(f"Cookie: {kudos_auth_cookie}")
        else:
            print("KuDoSAuth cookie not found. Ensure login was successful.")
        return {"crsid": username, "auth": kudos_auth_cookie['value'] }




def load_supervisions(file_path):
    """
    Load supervisions from a JSON file.
    
    Args:
        file_path (str): Path to the JSON file
        
    Returns:
        list: List of supervision objects
    """
    try:
        with open(file_path, 'r') as file:
            return json.load(file)
    except FileNotFoundError:
        print(f"Error: File not found at {file_path}")
        return []
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON format in {file_path}")
        return []

def parse_datetime(dt_string):
    """
    Parse datetime string and ensure it's timezone aware.
    If no timezone is specified, assume UTC.
    """
    try:
        # Try parsing with timezone
        dt = datetime.fromisoformat(dt_string)
        if dt.tzinfo is None:
            # If no timezone was provided, assume UTC
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        # Handle any parsing errors by returning a far future date
        print(f"Warning: Could not parse datetime: {dt_string}")
        return datetime.max.replace(tzinfo=timezone.utc)


def filter_supervisions(supervisions, target_tripos):
    """
    Filter supervisions based on tripos and booking criteria.
    
    Args:
        supervisions (list): List of supervision objects matching the schema
        target_tripos (str): The tripos to filter for
        
    Returns:
        list: Filtered list of supervisions
    """
    def has_student_in_tripos(supervision, tripos):
        # Check if any student in the supervision is in the target tripos
        return any(
            supervisee['tripos'] == tripos 
            for supervisee in supervision['group']
        )
    
    def check_booking_criteria(supervision):
        bookings = supervision['bookings']
        total_duration = sum(booking['duration'] for booking in bookings)
        minutes_allocated = supervision['minutesAllocated']
        
        # If total duration is less than allocated, return True
        if total_duration < minutes_allocated:
            return True
            
        # If total duration equals allocated, check for future bookings
        if total_duration == minutes_allocated:
            now = datetime.now(timezone.utc)
            
            # Parse booking times and compare
            return any(
                parse_datetime(booking['startTime']) > now 
                for booking in bookings
            )
            
        return False
    
    filtered_supervisions = [
        supervision for supervision in supervisions
        if has_student_in_tripos(supervision, target_tripos) and 
           check_booking_criteria(supervision)
    ]
    
    return filtered_supervisions

def load_config():
    """
    Load configuration from config.json file.
    Expected format: {"crsid": "abc123", "auth": "tok" }
    """
    if not os.path.exists('config.json'):
        with open('config.json', 'w') as f:
            json.dump(login(), f)

    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
            if 'crsid' not in config:
                raise ValueError("Config file must contain 'crsid' field")
            return config
    except FileNotFoundError:
        raise FileNotFoundError("Config file not found. Please create config.json with your CRSID")
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON in config file")

INFOFILE_NAME = "infofile.tex"
WORKFILE_NAME = "work.tex"

def process_infofile(directory):
    # Construct the full path to the infofile
    infofile_path = os.path.join(directory, INFOFILE_NAME)

    # Read the content of the infofile
    if not os.path.isfile(infofile_path):
        print(f"Error: {INFOFILE_NAME} not found in {directory}")
        return False

    with open(infofile_path, "r") as f:
        content = f.read()

    # Extract the svuploadkey value
    match = re.search(r"\\newcommand{\\svuploadkey}{(https?://[^\s]+)}", content)
    if not match:
        return True

    svuploadkey = match.group(1)

    # Download the file from svuploadkey
    config = load_config()
    response = requests.get(svuploadkey,  cookies={"KuDoSAuth": config['auth']})
    if response.status_code != 200:
        print(f"Error: Failed to download from {svuploadkey}, supo is not booked (HTTP {response.status_code})")
        return False

    # Replace the infofile.tex content with the downloaded content
    with open(infofile_path, "wb") as f:
        f.write(response.content)
    print(f"Replaced {INFOFILE_NAME} with the downloaded content.")

    return True

def compile_latex(directory):
    # Run tectonic to compile work.tex
    workfile_path = os.path.join(directory, WORKFILE_NAME)
    if not os.path.isfile(workfile_path):
        print(f"Error: {WORKFILE_NAME} not found in {directory}")
        return False

    try:
        subprocess.run(
            ["tectonic", WORKFILE_NAME ],
            cwd=directory,
            check=True,
        )
        print("LaTeX compilation successful.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error: LaTeX compilation failed. {e}")
        return False

def upload_pdf(directory):
    # Check if the PDF file exists
    pdf_path = os.path.join(directory, WORKFILE_NAME.replace(".tex", ".pdf"))
    if not os.path.isfile(pdf_path):
        print(f"Error: Compiled PDF not found: {pdf_path}")
        return False

    # Post the PDF file to the DUMMY_URL
    config = load_config()
    with open(pdf_path, "rb") as f:
        response = requests.post("https://kudos.chu.cam.ac.uk/kudos/rest/supervisions/upload", data=f.read(), cookies={"KuDoSAuth": config['auth']})

    if response.status_code == 200:
        print("PDF uploaded successfully.")
        return True
    else:
        print(f"Error: Failed to upload PDF (HTTP {response.status_code})")
        print(response.text)
        return False

def find_student_by_crsid(course_entry, target_crsid):
    """
    Find student details in the supervision entry matching the CRSID.
    Returns None if not found.
    """
    for supervisee in course_entry['supervisees']:
        if supervisee['user']['CRSID'] == target_crsid:
            return supervisee['user']
    return None

def fetch_booking(course_entry, slot_idx, synthesise_slot):
    """
    Set up supervision working directory and fetch or create info file.
    
    Args:
        course_entry: The supervision entry containing all supervision details
        slot_idx: The index of the slot (0-based)
        synthesise_slot: Boolean indicating if this is a synthetic slot
    """
    # Load config and get CRSID
    config = load_config()
    student_crsid = config['crsid']
    
    # Find student details
    student = find_student_by_crsid(course_entry, student_crsid)
    if not student:
        raise ValueError(f"Student with CRSID {student_crsid} not found in supervision group")
    
    # Extract course name from the first group entry
    course_name = course_entry['group'][0]['course']
    
    # Create directory name (1-indexed slot number)
    dir_name = f"{course_name}_{slot_idx + 1}"
    
    # Create directory if it doesn't exist

    if os.path.exists(dir_name):
        if (input("Path exists, compile and upload to KuDoS (y/n)?") == "y"):
            if process_infofile(dir_name):
                if compile_latex(dir_name):
                    upload_pdf(dir_name)
        return
    
    os.makedirs(dir_name, exist_ok=True)
    # Copy template file
    template_path = Path("template/perSV_mywork.tex")
    if not template_path.exists():
        raise FileNotFoundError("Template file not found: template/perSV_mywork.tex")
    
    shutil.copy(template_path, Path(dir_name) / "work.tex")
    
    if synthesise_slot:
        create_synthetic_info(course_entry, slot_idx + 1, dir_name, student)
    else:
        fetch_remote_info(course_entry, slot_idx, dir_name)

def create_synthetic_info(course_entry, slot_num, dir_name, student):
    """
    Create a synthetic info file for slots that don't exist yet.
    """
    # Get supervisor details
    supervisor = course_entry['supervisor']
    supervisor_name = f"{supervisor['title']} {supervisor['firstName']} {supervisor['lastName']}"
    
    # Format student name
    student_name = f"{student['title']} {student['firstName']} {student['lastName']}"
    student_crsid = student['CRSID']
    
    # Get course name
    course_name = course_entry['group'][0]['course']
    supervisor_crsid = course_entry['supervisor']['CRSID']
    group_id = course_entry['groupNumber']
    
    url = f"https://kudos.chu.cam.ac.uk/kudos/rest/supervisions/infofile/{supervisor_crsid}/{group_id}/{slot_num}"
    
    # Create info file content
    info_content = f"""\\newcommand{{\\svcourse}}{{{course_name}}}
\\newcommand{{\\svnumber}}{{{slot_num}}}
\\newcommand{{\\svvenue}}{{}}
\\newcommand{{\\svdate}}{{}}
\\newcommand{{\\svtime}}{{}}
\\newcommand{{\\svuploadkey}}{{{url}}}

\\newcommand{{\\svrname}}{{{supervisor_name}}}
\\newcommand{{\\jkfside}}{{oneside}}
\\newcommand{{\\jkfhanded}}{{right}}

\\newcommand{{\\studentname}}{{{student_name}}}
\\newcommand{{\\studentemail}}{{{student_crsid}}}
"""
    
    # Write info file
    info_path = Path(dir_name) / "infofile.tex"
    with open(info_path, 'w') as f:
        f.write(info_content)
    
    print(f"Created synthetic info file in {dir_name}")

def fetch_remote_info(course_entry, slot_idx, dir_name):
    """
    Fetch info file from remote server for existing bookings.
    """
    supervisor_crsid = course_entry['supervisor']['CRSID']
    group_id = course_entry['groupNumber']
    
    url = f"https://kudos.chu.cam.ac.uk/kudos/rest/supervisions/infofile/{supervisor_crsid}/{group_id}/{slot_idx + 1}"
    config = load_config()
    try:
        response = requests.get(url, cookies={"KuDoSAuth": config['auth']})
        response.raise_for_status()
        
        # Write the fetched content to infotile.tex
        info_path = Path(dir_name) / "infofile.tex"
        with open(info_path, 'w') as f:
            f.write(response.text)
        
        print(f"Fetched remote info file to {dir_name}")
        
    except requests.exceptions.RequestException as e:
        print(f"Error fetching info file: {e}")
        print("You may need to authenticate or check your connection.")

def get_unique_courses(filtered_supervisions):
    """Extract unique courses from the supervision list"""
    courses = set()
    for supervision in filtered_supervisions:
        for group in supervision['group']:
            courses.add((group['course'], group['subject'], group['tripos']))
    return sorted(list(courses))

def calculate_available_slots(supervision):
    """Calculate total available slots including both booked and potential slots"""
    booked_minutes = sum(booking['duration'] for booking in supervision['bookings'])
    total_slots = len(supervision['bookings'])
    
    # Calculate additional potential 60-minute slots
    remaining_minutes = supervision['minutesAllocated'] - booked_minutes
    additional_slots = remaining_minutes // 60
    
    return total_slots + additional_slots

def select_supervision_slot(filtered_supervisions):
    """Interactive function to select a course and supervision slot"""
    
    # Get unique courses
    courses = get_unique_courses(filtered_supervisions)
    
    # Display course options
    print("\nAvailable courses:")
    for idx, (course, subject, tripos) in enumerate(courses, 1):
        print(f"{idx}. {course} ({subject} - {tripos})")
    print(str(len(courses) +1)+  ". View marked work")
    
    # Get course selection
    while True:
        try:
            course_idx = int(input("\nSelect course number: ")) - 1
            if 0 <= course_idx < len(courses):
                selected_course = courses[course_idx]
                break
            if (course_idx == len(courses)): return True
            print("Invalid selection. Please try again.")
        except ValueError:
            print("Please enter a valid number.")
    
    # Filter supervisions for selected course
    course_supervisions = [
        sup for sup in filtered_supervisions
        if any(g['course'] == selected_course[0] for g in sup['group'])
    ]
    
    # Display supervision options
    print("\nAvailable supervisions:")
    for sup_idx, supervision in enumerate(course_supervisions):
        supervisor = supervision['supervisor']['name']
        booked_slots = [
            f"Slot {idx + 1}: {booking['startTime']} at {booking['venue']}"
            for idx, booking in enumerate(supervision['bookings'])
        ]
        
        total_booked = sum(b['duration'] for b in supervision['bookings'])
        remaining_minutes = supervision['minutesAllocated'] - total_booked
        additional_slots = remaining_minutes // 60
        
        print(f"\nSupervision Group {supervision['groupNumber']} with {supervisor}:")
        for slot in booked_slots:
            print(f"  {slot}")
        
        if additional_slots > 0:
            print(f"  {additional_slots} additional unbooked slot(s) available")
    
    # Get supervision and slot selection
    while True:
        try:
            if True:
                selected_supervision = course_supervisions[0]
                max_slots = calculate_available_slots(selected_supervision)
                
                slot_idx = int(input(f"\nSelect slot number (1-{max_slots}): ")) - 1
                if 0 <= slot_idx < max_slots:
                    # Determine if this is a synthetic slot
                    is_synthetic = slot_idx >= len(selected_supervision['bookings'])
                    
                    # Call the booking function
                    fetch_booking(selected_supervision, slot_idx, is_synthetic)
                    break
                print(f"Invalid slot number. Please choose between 1 and {max_slots}.")
            else:
                print("Invalid supervision number. Please try again.")
        except ValueError:
            print("Please enter valid numbers.")
    return False

import requests
from datetime import datetime

def fetch_supervisions():
    """Fetch supervisions from the API"""
    config = load_config()
    url = "https://kudos.chu.cam.ac.uk/kudos/rest/supervisions/upload-marked"
    try:
        response = requests.get(url, cookies={"KuDoSAuth": config['auth']})
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Error fetching data: {e}")
        return []


def filter_recent_supervisions(supervisions):
    """Filter supervisions to last 4 weeks"""
    four_weeks_ago = datetime.now(timezone.utc) - timedelta(weeks=4)
    return [
        sv for sv in supervisions 
        if parse_datetime(sv['start']) > four_weeks_ago
    ]

def display_supervisions(supervisions):
    """Display supervisions and return them sorted by date"""
    # Sort supervisions by date (newest last)
    sorted_supervisions = sorted(
        supervisions,
        key=lambda x: parse_datetime(x['start'])
    )
    
    # Print header
    print("\nSupervisions (newest at bottom):")
    print("-" * 80)
    print(f"{'Index':<6} {'Date':<25} {'CRSID':<10} {'Supervisor':<10} {'Group':<6} {'SV#':<4} {'Status'}")
    print("-" * 80)
    
    # Print each supervision
    for idx, sv in enumerate(sorted_supervisions):
        status = "Failed" if sv['failed'] else "Success"
        print(f"{idx:<6} {sv['start']:<25} {sv['CRSID']:<10} "
              f"{sv['supervisorCRSID']:<10} {sv['groupNumber']:<6} "
              f"{sv['svNumber']:<4} {status}")
    
    return sorted_supervisions

def select_supervision(supervisions):
    """Get user selection via input"""
    while True:
        try:
            print("\nEnter the index number of the supervision you want to view:")
            idx = int(input("> "))
            if 0 <= idx < len(supervisions):
                return supervisions[idx]
            print(f"Please enter a number between 0 and {len(supervisions)-1}")
        except ValueError:
            print("Please enter a valid number")

def open_url(url):
    """Open URL using system commands"""
    if sys.platform == 'darwin':    # macOS
        os.system(f'open "{url}"')
    elif sys.platform == 'win32':   # Windows
        os.system(f'start "{url}"')
    else:                           # Linux and others
        os.system(f'xdg-open "{url}"')

def parse_date(date_str):
    """Parse date string to datetime object"""
    return datetime.fromisoformat(date_str.replace('Z', '+00:00'))

def main2():
    # Fetch data
    supervisions = fetch_supervisions()
    if not supervisions:
        return
    
    # Display table
    sorted_supervisions = display_supervisions(filter_recent_supervisions(supervisions))
    
    # Select supervision
    selected = select_supervision(sorted_supervisions)
    
    # Open in browser
    url = f"https://kudos.chu.cam.ac.uk/kudos/rest/supervisions/upload-marked/{selected['uuid']}"
    open_url(url)

def main():
    print("KuDoS CLI 1.0")
    config = load_config()

    response = requests.get("https://kudos.chu.cam.ac.uk/kudos/rest/users/defaults", cookies={"KuDoSAuth": config['auth']})
    if (response.status_code != 200):
        print("KuDoS error!")
        return
    target_tripos = json.loads(response.text)['tripos']

    response = requests.get("https://kudos.chu.cam.ac.uk/kudos/rest/supervisions/getSVAssignments", cookies={"KuDoSAuth": config['auth']})
    if (response.status_code != 200):
        print("KuDoS error!")
        return


    
    # Load supervisions from file
    supervisions = json.loads(response.text)
    if not supervisions:
        return
    
    # Apply filters
    filtered_results = filter_supervisions(supervisions, target_tripos)
    

    if (select_supervision_slot(filtered_results)):
        main2()

if __name__ == "__main__":
    main()
