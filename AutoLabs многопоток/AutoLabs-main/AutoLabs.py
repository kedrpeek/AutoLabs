import argparse
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager
from TempMail import TempMail
import time
import re
import random
import string
from tqdm import tqdm
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException, NoSuchElementException
import os
import logging
import html
import pyperclip

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("registration.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("ElevenLabsRegistration")

# Настройка логирования ошибок
logging.basicConfig(filename='autolabs_errors.log', level=logging.ERROR, format='%(asctime)s %(levelname)s:%(message)s')

def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy", action="store_true", help="Use a random proxy from the site!")
    parser.add_argument("--count", type=int, default=1, help="How many accounts to create in one run (default: 1)")
    parser.add_argument("--profile-path", dest="profile_path", help="Path to Chrome user data dir to reuse existing profile")
    parser.add_argument("--profile-dir", dest="profile_dir", default="Default", help="Profile directory name inside user data dir (default: Default)")
    parser.add_argument("--signup-url", dest="signup_url", default="https://beta.elevenlabs.io/sign-up", help="Override sign-up page URL")
    parser.add_argument("--slot", type=int, default=-1, help="Window slot index (0-5) for 3x2 grid layout")
    parser.add_argument("--wait-captcha", action="store_true", help="Wait until captcha disappears (optional)")
    return parser.parse_args()

# Function to generate a random password
def generate_password(length):
    letters = string.ascii_letters
    digits = string.digits
    special_characters = "!@#$%^&*()-_=+[]{}|;:,.<>?"
    
    # Ensure password has at least one digit and one special character
    password = ''.join(random.choice(letters) for i in range(length - 2))
    password += random.choice(digits)  # Add one digit
    password += random.choice(special_characters)  # Add one special character
    
    # Shuffle the password to make sure special character and digit are not always at the end
    password_list = list(password)
    random.shuffle(password_list)
    return ''.join(password_list)

# Function to get a list of working HTTP/HTTPS proxies
def get_proxies():
    url = "https://www.sslproxies.org"
    response = requests.get(url)
    soup = BeautifulSoup(response.text, "html.parser")
    proxy_table = soup.find("table", class_="table table-striped table-bordered")
    if proxy_table:
        rows = proxy_table.find_all("tr")
        proxies = []
        for row in rows[1:]:
            columns = row.find_all("td")
            if len(columns) >= 2:
                ip = columns[0].text
                port = columns[1].text
                proxy = f"{ip}:{port}"
                proxies.append(proxy)
        return proxies
    else:
        return []

# Универсальная функция для повторных попыток
def try_until_success(action, max_attempts=30, delay=1, error_message=None, driver=None):
    for attempt in range(max_attempts):
        try:
            result = action()
            if result is not None or attempt == 0:  # Если действие успешно или это первая попытка
                return result
        except Exception as e:
            if attempt == max_attempts - 1:
                if error_message:
                    logging.error(f"{error_message}: {e}")
                    print(f"[ERROR] {error_message}")
                    print("Нажмите Enter для продолжения...")
                    input()
                else:
                    logging.error(f"Action failed after {max_attempts} attempts: {e}")
                    print(f"[ERROR] Action failed after {max_attempts} attempts")
                    print("Нажмите Enter для продолжения...")
                    input()
                return None
            print(f"Попытка {attempt + 1}/{max_attempts} не удалась: {e}. Повторяю через {delay} сек...")
            time.sleep(delay)
    return None

# Универсальная функция для поиска и клика по элементу
def find_and_click_element(driver, xpaths, max_attempts=30, delay=1, element_name="элемент"):
    def click_action():
        for xpath in xpaths:
            try:
                element = driver.find_element(By.XPATH, xpath)
                driver.execute_script("arguments[0].click();", element)
                print(f"Успешно кликнул по {element_name} с XPath: {xpath}")
                return True
            except Exception as e:
                continue
        raise Exception(f"Не удалось найти {element_name} ни с одним из XPath")
    
    return try_until_success(click_action, max_attempts, delay, f"Не удалось кликнуть по {element_name}")

# Универсальная функция для поиска и заполнения поля
def find_and_fill_element(driver, xpaths, text, max_attempts=30, delay=1, element_name="поле"):
    def fill_action():
        for xpath in xpaths:
            try:
                element = driver.find_element(By.XPATH, xpath)
                element.clear()
                element.send_keys(text)
                print(f"Успешно заполнил {element_name} с XPath: {xpath}")
                return True
            except Exception as e:
                continue
        raise Exception(f"Не удалось найти {element_name} ни с одним из XPath")
    
    return try_until_success(fill_action, max_attempts, delay, f"Не удалось заполнить {element_name}")

def wait_for_manual_captcha(driver, max_wait=300, check_interval=2):
    """Detect common captcha frames (hCaptcha, reCAPTCHA) and wait until they disappear.
    Prints messages when captcha is detected and when it is solved.
    Returns True if captcha solved/disappeared, False on timeout.
    """
    captcha_xpaths = [
        "//iframe[contains(@src, 'hcaptcha')]",
        "//iframe[contains(@src, 'recaptcha')]",
        "//iframe[contains(@src, 'api2/anchor')]",
        "//div[contains(@class, 'hcaptcha')]",
        "//div[contains(@class, 'g-recaptcha')]",
    ]

    # Quick check: is captcha present right now?
    def _is_captcha_present():
        # If token already present – solved
        if _captcha_token_present(driver):
            return False

        frames = driver.find_elements(By.TAG_NAME, 'iframe')
        for fr in frames:
            src = fr.get_attribute('src') or ''
            if any(k in src for k in ['hcaptcha', 'recaptcha']):
                try:
                    driver.switch_to.frame(fr)
                    # checkbox element may indicate solved
                    anchor_candidates = driver.find_elements(By.CSS_SELECTOR, '#recaptcha-anchor, div.hcaptcha-checkbox, div#checkbox, div.recaptcha-checkbox-border')
                    if anchor_candidates:
                        anchor = anchor_candidates[0]
                        checked = anchor.get_attribute('aria-checked')
                        cls = anchor.get_attribute('class') or ''
                        if checked == 'true' or 'is-checked' in cls or 'recaptcha-checkbox-checked' in cls:
                            # solved – continue scanning others
                            pass
                        else:
                            return True  # unsolved checkbox
                except Exception:
                    return True  # cannot access, assume unsolved
                finally:
                    driver.switch_to.default_content()

        # No unsolved captcha frame detected – consider solved
        return False

    # Wait until captcha first appears (max 10s). If never appears, simply exit.
    start_time = time.time()
    while time.time() - start_time < 10:
        if _is_captcha_present():
            print("[CAPTCHA] Captcha detected. Please solve it manually – script will pause until it disappears…")
            break
        time.sleep(0.5)
    else:
        # No captcha showed – nothing to wait for
        return True

    # Wait for captcha element(s) to disappear signalling it was solved
    start_time = time.time()
    while time.time() - start_time < max_wait:
        if not _is_captcha_present():
            print("[CAPTCHA] Captcha appears solved – continuing script.")
            return True
        time.sleep(check_interval)
    print("[CAPTCHA] Timeout waiting for captcha to be solved ({}s). Continuing anyway…".format(max_wait))
    return False

# Add helper functions after wait_for_manual_captcha

def _find_captcha_iframes(driver):
    """Return list of captcha-related iframe WebElements present on page."""
    frames = driver.find_elements(By.TAG_NAME, 'iframe')
    result = []
    for fr in frames:
        src = fr.get_attribute('src') or ''
        if any(k in src for k in ['hcaptcha', 'recaptcha', 'api2/anchor']):
            result.append(fr)
    return result


def click_captcha_checkbox(driver, max_attempts=5):
    """Try to enter captcha iframe and click checkbox to open challenge."""
    for attempt in range(max_attempts):
        frames = _find_captcha_iframes(driver)
        if not frames:
            return False  # no captcha detected
        for fr in frames:
            try:
                driver.switch_to.frame(fr)
                # common selectors for anchor/checkbox
                possible = [
                    (By.ID, 'recaptcha-anchor'),
                    (By.CSS_SELECTOR, 'div#checkbox'),
                    (By.CSS_SELECTOR, 'div.hcaptcha-checkbox'),
                    (By.CSS_SELECTOR, 'div.recaptcha-checkbox-border')
                ]
                for how, sel in possible:
                    elems = driver.find_elements(how, sel)
                    if elems:
                        driver.execute_script("arguments[0].click();", elems[0])
                        driver.switch_to.default_content()
                        print('[CAPTCHA] Checkbox clicked to open challenge')
                        return True
            except Exception:
                pass
            finally:
                driver.switch_to.default_content()
        time.sleep(1)
    print('[CAPTCHA] Could not click captcha checkbox automatically')
    return False


def handle_captcha_and_resubmit(driver):
    """Detect captcha, click checkbox, wait for solution, then re-submit form."""
    if not _find_captcha_iframes(driver):
        return  # no captcha at all
    print('[CAPTCHA] Captcha detected, trying to open…')
    click_captcha_checkbox(driver)
    # Give user time to solve
    wait_for_manual_captcha(driver, max_wait=900, check_interval=3)
    print('[CAPTCHA] Attempting to re-submit Sign up after captcha…')
    try:
        try_until_success(click_signup_button, max_attempts=5, delay=2)
    except Exception:
        pass
    print('[CAPTCHA] Re-submit done.')

args = parse_arguments()

# Create a new TempMail object
print("Creating a new TempMail object...")
tmp = TempMail()

# Generate an inbox
print("Generating an inbox...")
inb = TempMail.generateInbox(tmp)

# Generate a random password
password = generate_password(10)

# Store accounts in the same directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
accounts_file = os.path.join(SCRIPT_DIR, "accounts.txt")

# Сохраняем данные сразу после создания
# Комментируем первый момент записи, чтобы не создавать черновую запись без API
# print("Saving account data...")
# with open(accounts_file, "a", encoding="utf-8") as file:
#     file.write("---------------------------\n")
#     file.write(f"Email:    {inb.address}\n")
#     file.write(f"Password: {password}\n")
#     file.write(f"Api: \n")
#     file.write("---------------------------\n")
# print(f"Account data saved: Email: {inb.address}, Password: {password}")

# Define the URL of the sign-up page
signup_url = args.signup_url

# Set the proxy options for the Chrome driver
chrome_options = webdriver.ChromeOptions()

# If the user wants to reuse an existing Chrome profile (helps to avoid CAPTCHAs)
if args.profile_path:
    chrome_options.add_argument(f"--user-data-dir={args.profile_path}")
    chrome_options.add_argument(f"--profile-directory={args.profile_dir}")

# Baseline flags (keep browser visible)
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-features=NetworkService")
chrome_options.add_argument("--window-size=1920x1080")
chrome_options.add_argument("--disable-extensions")

if args.proxy:
    # Get a list of working HTTP/HTTPS proxies
    proxies = get_proxies()
    print("Total Proxies:", len(proxies))

    # Select a random proxy from the list
    if proxies:
        proxy = random.choice(proxies)
        print("Selected Proxy:", proxy)
        chrome_options.add_argument(f"--proxy-server=http://{proxy}")
    else:
        print("No working proxies found.")

# Create a new instance of the Chrome driver with proxy options
print("Creating a new instance of the Chrome driver...")
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

# After driver initialization
if args.slot >= 0:
    # Distribute windows in a 3x2 grid (columns x rows) → 6 slots total
    try:
        cols, rows = 3, 2
        screen_width = driver.execute_script("return screen.width") or 1920
        screen_height = driver.execute_script("return screen.height") or 1080

        cell_width = int(screen_width / cols)
        cell_height = int(screen_height / rows)

        col = args.slot % cols
        row = (args.slot // cols) % rows  # wrap-around if slot >5

        x_pos = col * cell_width
        y_pos = row * cell_height

        driver.set_window_size(cell_width, cell_height)
        driver.set_window_position(x_pos, y_pos)
    except Exception as e:
        print(f"[WARN] Failed to set window position/size: {e}")

# Go to the sign-up page
print("Navigating to the sign-up page...")
driver.get(signup_url)

# Wait for the page to load
wait = WebDriverWait(driver, 30)

# Try to handle cookie consent if it appears
try:
    print("Checking for cookie consent popup...")
    cookie_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accept') or contains(text(), 'accept') or contains(text(), 'Allow') or contains(text(), 'OK') or contains(text(), 'Ok')]")))
    print("Clicking cookie consent button...")
    driver.execute_script("arguments[0].click();", cookie_button)
    time.sleep(2)
except (TimeoutException, NoSuchElementException):
    print("No cookie consent popup found or it was already handled.")

# Find the email input field and enter the temporary email
print("Entering the temporary email...")
email_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[name="email"]')))
email_field.send_keys(inb.address)

# Find the password input field and enter the password
print("Entering the password...")
password_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[name="password"]')))
password_field.send_keys(password)

# Find the terms checkbox and click it using multiple methods
print("Checking the terms checkbox...")
def click_terms_checkbox():
    # Метод 1: Точный XPath
    try:
        exact_terms_xpath = '//*[@id="app-root"]/div[2]/div/div[2]/div/div/form/div[3]/div/div[2]/div/label/button/div'
        terms_element = driver.find_element(By.XPATH, exact_terms_xpath)
        driver.execute_script("arguments[0].click();", terms_element)
        print("Clicked on terms checkbox with exact XPath")
        return True
    except Exception as e:
        print(f"Method 1 failed: {e}")
        
    # Метод 2: По классу
    try:
        checkbox_div = driver.find_element(By.CSS_SELECTOR, "div.checkbox-hitarea.overlay.-inset-1\\.5")
        driver.execute_script("arguments[0].click();", checkbox_div)
        print("Clicked on checkbox using class selector")
        return True
    except Exception as e:
        print(f"Method 2 failed: {e}")
    
    # Метод 3: JavaScript
    try:
        driver.execute_script("document.querySelector('input[name=\"terms\"]').checked = true;")
        print("Set checkbox via JavaScript")
        return True
    except Exception as e:
        print(f"Method 3 failed: {e}")
    
    # Метод 4: Поиск всех чекбоксов
    try:
        checkboxes = driver.find_elements(By.XPATH, "//input[@type='checkbox']")
        if checkboxes:
            for checkbox in checkboxes:
                try:
                    driver.execute_script("arguments[0].click();", checkbox)
                    print(f"Clicked on checkbox with name: {checkbox.get_attribute('name')}")
                    return True
                except:
                    continue
    except Exception as e:
        print(f"Method 4 failed: {e}")
    
    raise Exception("All checkbox methods failed")

try_until_success(click_terms_checkbox, max_attempts=10, delay=2, error_message="Не удалось поставить галочку в чекбоксе")

time.sleep(2)

# Find the sign-up button and click it
print("Looking for the sign-up button...")
def click_signup_button():
    # Метод 1: Точный XPath
    try:
        signup_button_xpath = '//*[@id="app-root"]/div[2]/div/div[2]/div/div/form/div[6]/button'
        signup_button = driver.find_element(By.XPATH, signup_button_xpath)
        driver.execute_script("arguments[0].click();", signup_button)
        print("Clicked on sign-up button with exact XPath")
        return True
    except Exception as e:
        print(f"Signup button method 1 failed: {e}")
    
    # Метод 2: Отправка формы
    try:
        driver.execute_script("document.querySelector('form').submit();")
        print("Form submitted via JS")
        return True
    except Exception as e:
        print(f"Signup button method 2 failed: {e}")
    
    # Метод 3: Поиск по тексту
    try:
        signup_button = driver.find_element(By.XPATH, "//button[contains(text(), 'Sign up') or contains(text(), 'Sign Up') or contains(text(), 'Create Account')]")
        driver.execute_script("arguments[0].click();", signup_button)
        print("Clicked on sign-up button by text")
        return True
    except Exception as e:
        print(f"Signup button method 3 failed: {e}")
    
    raise Exception("All signup button methods failed")

try_until_success(click_signup_button, max_attempts=10, delay=2, error_message="Не удалось нажать кнопку регистрации")

# NEW captcha handling & re-submit
handle_captcha_and_resubmit(driver)

print("Registration form submitted successfully!")

# --- УБРАНО: print("Waiting 15 seconds for manual CAPTCHA completion...") и задержка ---
# Вместо этого сразу начинаем слушать почту с коротким интервалом

print("Registration completed!")

# Ждем письмо и переходим по ссылке подтверждения
print("Waiting for confirmation email...")
max_email_check_attempts = 60  # увеличим число попыток, чтобы не прерываться рано
email_check_delay = 2  # интервал между проверками (секунды)

def extract_confirmation_link(email_body):
    try:
        email_body = html.unescape(email_body)
    except:
        pass
    
    patterns = [
        r'https?://[^\s<>"\']+(?:confirm|verify|activate)[^\s<>"\']*',
        r'<a[^>]*href=["\'](https?://[^\s<>"\']+)["\'][^>]*>.*?(?:confirm|verify|activate).*?</a>',
        r'<a[^>]*href=["\'](https?://[^\s<>"\']+)["\'][^>]*>',
        r'https?://[^\s<>"\']+',
    ]
    
    all_links = []
    for i, pattern in enumerate(patterns):
        matches = re.findall(pattern, email_body, re.IGNORECASE)
        if matches:
            if i == 1:
                all_links.extend([m for m in matches])
            else:
                all_links.extend(matches)
    
    filtered_links = []
    for link in all_links:
        if link == "https://elevenlabs.io" or link == "http://elevenlabs.io":
            continue
        if any(domain in link for domain in ["facebook.com", "twitter.com", "instagram.com", 
                                           "linkedin.com", "youtube.com", "mailto:", 
                                           "unsubscribe", "privacy", "terms"]):
            continue
        filtered_links.append(link)
    
    if filtered_links:
        confirm_links = [link for link in filtered_links if any(word in link.lower() 
                                                               for word in ["confirm", "verify", "activate"])]
        if confirm_links:
            return max(confirm_links, key=len)
        return max(filtered_links, key=len)
    
    return None

# Проверка писем
success = False
for attempt in range(max_email_check_attempts):
    print(f"Checking for emails (attempt {attempt+1}/{max_email_check_attempts})...")
    try:
        emails = TempMail.getEmails(tmp, inbox=inb)
        print(f"Found {len(emails) if emails else 0} emails")
        if emails:
            try:
                for email_index, email in enumerate(emails):
                    print(f"Processing email {email_index + 1}/{len(emails)}")
                    email_body = email.body if hasattr(email, 'body') else str(email)
                    print(f"EMAIL BODY:\n{email_body}\n---END EMAIL---")
                    confirmation_link = extract_confirmation_link(email_body)
                    if confirmation_link:
                        print(f"CONFIRMATION LINK: {confirmation_link}")
                        print(f"Visiting the confirmation link: {confirmation_link}")
                        driver.get(confirmation_link)
                        time.sleep(5)
                        success = True
                        break
                if success:
                    break
            except Exception as e:
                print(f"Error processing emails: {e}")
        else:
            print("No emails found yet, waiting...")
    except Exception as e:
        print(f"Error checking emails: {e}")
    if attempt < max_email_check_attempts - 1 and not success:
        print(f"Waiting {email_check_delay} seconds before checking again...")
        time.sleep(email_check_delay)

if success:
    print("Starting onboarding process...")
    
    # 1. Нажимаем кнопку после перехода по ссылке
    def click_dialog_button():
        dialog_xpaths = [
            '//*[@id="headlessui-dialog-panel-:r1:"]/div[2]/button',
            '//button[contains(text(), "OK") or contains(text(), "Continue") or contains(text(), "Got it")]',
            '//div[@role="dialog"]//button'
        ]
        return find_and_click_element(driver, dialog_xpaths, max_attempts=10, delay=2, element_name="кнопка диалога")
    
    try_until_success(click_dialog_button, max_attempts=5, delay=3, error_message="Не удалось нажать кнопку диалога")
    time.sleep(3)
    
    # 2. Нажимаем Sign in
    def click_signin_button():
        signin_xpaths = [
            '/html/body/div[1]/div[2]/div[2]/div/a[1]/button',
            '//a[contains(@href, "sign-in")]/button',
            '//button[contains(text(), "Sign in") or contains(text(), "Sign In")]',
            '//a[contains(text(), "Sign in") or contains(text(), "Sign In")]'
        ]
        return find_and_click_element(driver, signin_xpaths, max_attempts=10, delay=2, element_name="кнопка входа")
    
    try_until_success(click_signin_button, max_attempts=5, delay=3, error_message="Не удалось нажать кнопку входа")
    time.sleep(3)
    
    # 3. Заполняем форму входа
    def fill_login_form():
        try:
            print("Filling login form...")
            email_login_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[name="email"]')))
            email_login_field.send_keys(inb.address)
            
            password_login_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[name="password"]')))
            password_login_field.send_keys(password)
            
            # Нажимаем Sign in в форме
            signin_form_xpaths = [
                '//*[@id="sign-in-form"]/div[3]/button',
                '//form//button[contains(text(), "Sign in") or contains(text(), "Sign In")]',
                '//button[@type="submit"]'
            ]
            return find_and_click_element(driver, signin_form_xpaths, max_attempts=10, delay=2, element_name="кнопка отправки формы входа")
        except Exception as e:
            raise Exception(f"Login form error: {e}")
    
    try_until_success(fill_login_form, max_attempts=5, delay=3, error_message="Не удалось заполнить форму входа")
    time.sleep(5)
    
    # 4. Онбординг
    # Шаг 1: Нажимаем Continue в начале онбординга
    def start_onboarding():
        try:
            print("Waiting 5 seconds for onboarding button to appear...")
            time.sleep(5)
            
            print("Step 1: Clicking Continue to start onboarding...")
            onboarding_xpaths = [
                '//*[@id="app-root"]/div[2]/div/div[3]/div/div/div/button',  # Continue кнопка
                '//button[contains(text(), "Continue") or contains(text(), "Get Started") or contains(text(), "Start")]',
                '//div[contains(@class, "onboarding")]//button'
            ]
            return find_and_click_element(driver, onboarding_xpaths, max_attempts=10, delay=2, element_name="кнопка Continue в начале онбординга")
        except Exception as e:
            raise Exception(f"Onboarding start error: {e}")
    
    try_until_success(start_onboarding, max_attempts=5, delay=3, error_message="Не удалось нажать Continue в начале онбординга")
    time.sleep(3)
    
    # Заполняем имя
    def fill_firstname():
        firstname_xpaths = [
            '//*[@id="firstname"]',
            '//input[@name="firstname"]',
            '//input[@placeholder*="name" or @placeholder*="Name"]'
        ]
        random_names = ["Alex", "Sam", "Jordan", "Taylor", "Casey", "Morgan", "Riley", "Quinn", "Avery", "Blake"]
        return find_and_fill_element(driver, firstname_xpaths, random.choice(random_names), max_attempts=10, delay=2, element_name="поле имени")
    
    try_until_success(fill_firstname, max_attempts=5, delay=2, error_message="Не удалось заполнить имя")
    
    # Заполняем день рождения
    def fill_birthday():
        day_xpaths = [
            '//*[@id="bday-day"]',
            '//input[@name="day"]',
            '//input[@placeholder*="day" or @placeholder*="Day"]'
        ]
        return find_and_fill_element(driver, day_xpaths, str(random.randint(10, 27)), max_attempts=10, delay=2, element_name="поле дня")
    
    try_until_success(fill_birthday, max_attempts=5, delay=2, error_message="Не удалось заполнить день")
    
    # Выбираем месяц
    def select_month():
        try:
            print("Selecting month...")
            month_button_xpaths = [
                '//*[@id="app-root"]/div[2]/div/div[3]/div/div[2]/form/div[2]/div/button',
                '//button[contains(@aria-label, "month") or contains(text(), "Month")]',
                '//select[@name="month"]'
            ]
            
            # Нажимаем на кнопку выбора месяца
            for xpath in month_button_xpaths:
                try:
                    month_button = driver.find_element(By.XPATH, xpath)
                    driver.execute_script("arguments[0].click();", month_button)
                    time.sleep(1)
                    break
                except:
                    continue
            
            # Выбираем случайный месяц
            xpaths_by_text = [
                '//div[@role="option" and normalize-space()="January"]',
                '//div[@role="option" and normalize-space()="February"]',
                '//div[@role="option" and normalize-space()="March"]',
                '//div[@role="option" and normalize-space()="April"]',
                '//div[@role="option" and normalize-space()="May"]',
                '//div[@role="option" and normalize-space()="June"]',
                '//div[@role="option" and normalize-space()="July"]',
                '//div[@role="option" and normalize-space()="August"]',
                '//div[@role="option" and normalize-space()="September"]',
                '//div[@role="option" and normalize-space()="October"]',
                '//div[@role="option" and normalize-space()="November"]',
                '//div[@role="option" and normalize-space()="December"]'
            ]
            month_index = random.randint(0, 11)
            month_xpath = xpaths_by_text[month_index]
            
            try:
                month_option = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, month_xpath))
                )
                month_option.click()
                time.sleep(1)
                return True
            except:
                # Если не удалось, пробуем выбрать через select
                try:
                    month_select = driver.find_element(By.XPATH, '//select[@name="month"]')
                    month_select.click()
                    month_select.send_keys(str(month_index + 1))
                    return True
                except:
                    return True  # Игнорируем ошибку выбора месяца
        except Exception as e:
            print(f"[IGNORED] Month selection error: {e}")
            return True
    
    try_until_success(select_month, max_attempts=3, delay=1, error_message="Не удалось выбрать месяц")
    
    # Заполняем год
    def fill_year():
        year_xpaths = [
            '//*[@id="bday-year"]',
            '//input[@name="year"]',
            '//input[@placeholder*="year" or @placeholder*="Year"]'
        ]
        return find_and_fill_element(driver, year_xpaths, "2000", max_attempts=10, delay=2, element_name="поле года")
    
    try_until_success(fill_year, max_attempts=5, delay=2, error_message="Не удалось заполнить год")
    
    # Нажимаем Continue после заполнения дат
    def click_continue():
        continue_xpaths = [
            '//*[@id="app-root"]/div[2]/div/div[3]/div/div[2]/form/button',
            '//button[contains(text(), "Continue") or contains(text(), "Next") or contains(text(), "Submit")]',
            '//form//button[@type="submit"]'
        ]
        return find_and_click_element(driver, continue_xpaths, max_attempts=10, delay=2, element_name="кнопка Continue")
    
    try_until_success(click_continue, max_attempts=5, delay=3, error_message="Не удалось нажать Continue")
    time.sleep(3)
    
    # Выбираем опции (4 клика подряд с правильными XPath)
    def select_options():
        print("Selecting options...")
        
        # Шаг 1: Первый выбор
        print("Step 1: Clicking first option...")
        option1_xpaths = [
            '//*[@id="app-root"]/div[2]/div/div[3]/div/div[2]/div/div[1]/button/div/div',  # Первый выбор
            '//div[contains(@class, "option") or contains(@class, "choice")]//button[1]//div/div'
        ]
        
        for xpath in option1_xpaths:
            try:
                option_button = driver.find_element(By.XPATH, xpath)
                driver.execute_script("arguments[0].click();", option_button)
                time.sleep(2)  # Задержка 2 секунды
                print("✓ Clicked first option")
                break
            except:
                continue
        
        # Шаг 2: Второй выбор
        print("Step 2: Clicking second option...")
        option2_xpaths = [
            '//*[@id="app-root"]/div[2]/div/div[3]/div/div[2]/div/div[1]/button/div/div',  # Второй выбор
            '//div[contains(@class, "option") or contains(@class, "choice")]//button[1]//div/div'
        ]
        
        for xpath in option2_xpaths:
            try:
                option_button = driver.find_element(By.XPATH, xpath)
                driver.execute_script("arguments[0].click();", option_button)
                time.sleep(2)  # Задержка 2 секунды
                print("✓ Clicked second option")
                break
            except:
                continue
        
        # Шаг 3: Третий выбор
        print("Step 3: Clicking third option...")
        option3_xpaths = [
            '//*[@id="app-root"]/div[2]/div/div[3]/div/div[2]/div/div[2]/button/div/div',  # Третий выбор
            '//div[contains(@class, "option") or contains(@class, "choice")]//button[2]//div/div'
        ]
        
        for xpath in option3_xpaths:
            try:
                option_button = driver.find_element(By.XPATH, xpath)
                driver.execute_script("arguments[0].click();", option_button)
                time.sleep(2)  # Задержка 2 секунды
                print("✓ Clicked third option")
                break
            except:
                continue
        
        # Шаг 4: Четвертый выбор
        print("Step 4: Clicking fourth option...")
        option4_xpaths = [
            '//*[@id="app-root"]/div[2]/div/div[3]/div/div[3]/div/div/div/div/button',  # Четвертый выбор
            '//div[contains(@class, "option") or contains(@class, "choice")]//button[3]//div/div'
        ]
        
        for xpath in option4_xpaths:
            try:
                option_button = driver.find_element(By.XPATH, xpath)
                driver.execute_script("arguments[0].click();", option_button)
                time.sleep(2)  # Задержка 2 секунды
                print("✓ Clicked fourth option")
                break
            except:
                continue
        
        # Шаг 5: Последний Skip
        print("Step 5: Clicking final Skip...")
        final_skip_xpaths = [
            '//*[@id="app-root"]/div[2]/div/div[3]/div/div/div/div[3]/div[2]/button[1]',  # Последний Skip
            '//button[contains(text(), "Skip") or contains(text(), "Continue")]',
            '//div[contains(@class, "onboarding")]//button'
        ]
        
        for xpath in final_skip_xpaths:
            try:
                skip_button = driver.find_element(By.XPATH, xpath)
                driver.execute_script("arguments[0].click();", skip_button)
                time.sleep(2)  # Задержка 2 секунды
                print("✓ Clicked final Skip button")
                break
            except:
                continue
        
        print("✓ All onboarding steps completed!")
        return True
    
    try_until_success(select_options, max_attempts=5, delay=2, error_message="Не удалось выбрать опции")
    
    # Убираем дублирующиеся функции завершения онбординга, так как он уже завершен выше
    time.sleep(2)
    
    # 5. Переходим к API ключам
    def create_api_key():
        try:
            print("Navigating to API keys...")
            driver.get("https://elevenlabs.io/app/settings/api-keys")
            time.sleep(5)
            
            # 1. Кнопка создать ключ
            def click_create_key():
                print("Clicking create key button...")
                create_xpaths = [
                    '//*[@id="content"]/div[3]/div/main/div/button',
                    '//button[contains(text(), "Create") or contains(text(), "New") or contains(text(), "Add")]',
                    '//main//button[1]'
                ]
                return find_and_click_element(driver, create_xpaths, max_attempts=10, delay=2, element_name="кнопка создания ключа")
            
            try_until_success(click_create_key, max_attempts=5, delay=3, error_message="Не удалось нажать кнопку создания ключа")
            time.sleep(2)
            
            # 2. Переключатель ограничений (универсальный XPath)
            def toggle_restrictions():
                print("Checking restrictions toggle (universal XPath)...")
                toggle_xpaths = [
                    '//button[@role="switch" and contains(@id, "restrict-key-toggle")]',
                    '//button[contains(@aria-label, "restrict") or contains(@aria-label, "toggle")]',
                    '//input[@type="checkbox"]'
                ]
                
                for xpath in toggle_xpaths:
                    try:
                        toggle_button = driver.find_element(By.XPATH, xpath)
                        aria_checked = toggle_button.get_attribute('aria-checked')
                        if aria_checked == 'true':
                            print("Toggling restrictions (currently ON)...")
                            driver.execute_script("arguments[0].click();", toggle_button)
                            time.sleep(1)
                        else:
                            print("Restrictions already OFF, skipping toggle.")
                        return True
                    except:
                        continue
                
                print("[WARNING] Не удалось найти переключатель ограничений. Продолжаю дальше...")
                return True
            
            try_until_success(toggle_restrictions, max_attempts=3, delay=1, error_message="Не удалось настроить ограничения")
            
            # 3. Кнопка создать в модалке (улучшенный поиск)
            def confirm_api_creation():
                print("Confirming API key creation...")
                try:
                    print("[Create-2] Пробую XPath: //button[normalize-space(text())='Create']")
                    button = driver.find_element(By.XPATH, "//button[normalize-space(text())='Create']")
                    driver.execute_script("arguments[0].click();", button)
                    print("[Create-2] ✓ Клик по кнопке Create (XPath)")
                except Exception as e:
                    print(f"[Create-2] Не сработал: {e}")
                print("[Create-DONE] Попытка завершена.")

            api_key = try_until_success(confirm_api_creation, max_attempts=50, delay=3, error_message="Не удалось подтвердить создание ключа")
            
            # Сохраняем API ключ в блок аккаунта, если он получен
            if api_key:
                block = (
                    "---------------------------\n"
                    f"Email:    {inb.address}\n"
                    f"Password: {password}\n"
                    f"Api: {api_key}\n"
                    "---------------------------\n"
                )
                with open(accounts_file, "a", encoding="utf-8") as file:
                    file.write(block)
                print("Account data saved successfully!")
                return True
            
            return False
            
        except Exception as e:
            raise Exception(f"API key creation general error: {e}")
    
    try_until_success(create_api_key, max_attempts=5, delay=5, error_message="Не удалось пройти этап создания API ключа")

print("Process completed!")
print("Done!")

def extract_api_key():
    print("Extracting API key...")
    import pyperclip
    import time
    api_key = None
    # 1. Пробуем получить ключ из input
    try:
        print("[API-1] Пробую получить ключ из input...")
        time.sleep(5)
        input_elem = driver.find_element(By.XPATH, "//input[contains(@value, 'sk_')]")
        api_key = input_elem.get_attribute('value')
        if api_key and api_key.startswith('sk_'):
            print(f"[API-1] ✓ Ключ получен из input: {api_key}")
            return api_key
    except Exception as e:
        print(f"[API-1] Не сработал: {e}")
    # 2. Кликнуть Copy и взять из буфера
    try:
        print("[API-2] Пробую кликнуть Copy и взять из буфера...")
        time.sleep(5)
        copy_btn = driver.find_element(By.XPATH, "//button[contains(., 'Copy') or contains(., 'Скопировать')]")
        driver.execute_script("arguments[0].click();", copy_btn)
        time.sleep(1)
        api_key = pyperclip.paste()
        if api_key and api_key.startswith('sk_'):
            print(f"[API-2] ✓ Ключ скопирован из буфера: {api_key}")
            return api_key
    except Exception as e:
        print(f"[API-2] Не сработал: {e}")
    # 3. Поиск по классу
    try:
        print("[API-3] Пробую найти по классу api-key/key-value...")
        time.sleep(5)
        api_key_element = driver.find_element(By.XPATH, '//*[contains(@class, "api-key") or contains(@class, "key-value")]')
        api_key = api_key_element.text
        if api_key and api_key.startswith('sk_'):
            print(f"[API-3] ✓ Ключ найден по классу: {api_key}")
            return api_key
    except Exception as e:
        print(f"[API-3] Не сработал: {e}")
    # 4. Поиск по тексту sk-
    try:
        print("[API-4] Пробую найти по тексту sk- ...")
        time.sleep(5)
        api_key_elements = driver.find_elements(By.XPATH, '//*[contains(text(), "sk-")]')
        for el in api_key_elements:
            if el.text.startswith('sk_'):
                api_key = el.text
                print(f"[API-4] ✓ Ключ найден по тексту: {api_key}")
                return api_key
    except Exception as e:
        print(f"[API-4] Не сработал: {e}")
    print("[API-FAIL] Не удалось получить API ключ ни одним из методов!")
    input("Нажмите Enter чтобы продолжить...")
    return None

# После получения ключа сохраняем его в файл
api_key = try_until_success(extract_api_key, max_attempts=1, delay=1, error_message="Не удалось получить API ключ")
if api_key:
    block = (
        "---------------------------\n"
        f"Email:    {inb.address}\n"
        f"Password: {password}\n"
        f"Api: {api_key}\n"
        "---------------------------\n"
    )
    with open(accounts_file, "a", encoding="utf-8") as file:
        file.write(block)
    print("API key saved successfully!")
else:
    print("[ERROR] API ключ не был получен и не сохранён!")

# -------------------------------------------------------------
# Ниже небольшой вспомогательный метод для повторного запуска
# регистрации без закрытия драйвера, чтобы можно было создать
# несколько аккаунтов подряд, задавая их количество через --count
# -------------------------------------------------------------

def restart_for_next_account():
    """Загружает страницу регистрации и создаёт новую почту/пароль"""
    driver.get(signup_url)
    time.sleep(3)  # короткая пауза, чтобы страница точно загрузилась

for current_idx in range(1, args.count):
    # После первой итерации создаём следующую
    print(f"\n===== Starting account {current_idx + 1}/{args.count} =====")
    restart_for_next_account()

    # Генерируем новые данные и повторяем ключевые шаги регистрации,
    # используя те же функции/блоки кода. Самый простой способ –
    # выполнить файл повторно во вложенном процессе, передав
    # остальные аргументы (без --count), а по завершении вернуться.
    # Чтобы не закрывать текущее окно браузера, используем exec.
    import subprocess, sys, shlex

    # Собираем аргументы без --count, чтобы внутренний запуск
    # создал ровно 1 аккаунт и не запустил новую цепочку.
    inner_args = [a for a in sys.argv[1:] if not a.startswith("--count")]
    cmd = [sys.executable, os.path.abspath(__file__)] + inner_args
    print("Launching nested process for next account:", " ".join(shlex.quote(a) for a in cmd))
    subprocess.run(cmd, check=True)

print("\nВсе аккаунты созданы. Скрипт завершён.")
