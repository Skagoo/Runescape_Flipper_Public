import selenium.webdriver as webdriver
import selenium.webdriver.support.ui as ui
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from aenum import Enum  #Had to use eanum lib since Python's build-in Enum was added in python 3.4
from datetime import datetime
import hmac, base64, struct, hashlib, time, json, copy, re, threading, os, sys, urllib2, logging, random, traceback

global available_worlds
global settings_path
available_worlds = [140, 139, 138, 134, 124, 123, 119, 116, 106, 105, 104, 103, 100, 99, 98, 96, 92, 91, 89, 88, 87, 85, 84, 83, 82, 79, 78, 74, 72, 71, 70, 69, 68, 67, 65, 64, 63, 62, 60, 59, 58, 56, 54, 53, 51, 50, 49, 46, 45, 44, 42, 40, 39, 37, 36, 35, 32, 31, 28, 27, 26, 25, 24, 23, 22, 21, 16, 15, 14, 12, 10, 9, 6, 5, 4, 2, 1]
settings_path = "settings.json"

class Authenticator:
    def get_hotp_token(self, secret, intervals_no):
        key = base64.b32decode(secret, True)
        msg = struct.pack(">Q", intervals_no)
        h = hmac.new(key, msg, hashlib.sha1).digest()
        o = ord(h[19]) & 15
        h = (struct.unpack(">I", h[o:o+4])[0] & 0x7fffffff) % 1000000
        return h

    def get_totp_token(self, secret):
        return self.get_hotp_token(secret, intervals_no=int(time.time())//30)

class Account:
    def __init__(self, username, password, authenticator_secret_key, id):
        self.username = username
        self.password = password
        self.authenticator_secret_key = authenticator_secret_key
        self.id = id

    @staticmethod
    def get_accounts_from_json():
        with open(settings_path) as data_file:
            settings = json.load(data_file)
            accounts = settings["accounts"]

            return accounts

class Slot:
    def __init__(self, id, slot_state, item):
        self.id = id
        self.state = slot_state
        self.item = item
        self.value = "0"

    def set_item(self, item):
        self.item = item

    def get_id(self):
        return self.id

    def set_value(self, value):
        self.value = value

class Slot_States(Enum):
    COMPLETE_BUYING = "slot clearfix ng-scope complete buying"
    COMPLETE_SELLING = "slot clearfix ng-scope complete selling"
    ABORTED_BUYING = "slot clearfix ng-scope aborted buying"
    ABORTED_SELLING = "slot clearfix ng-scope aborted selling"
    BUYING = "slot clearfix ng-scope buying"
    SELLING = "slot clearfix ng-scope selling"
    EMPTY = "slot clearfix ng-scope empty"  

class Item:
    def __init__(self, name, min_price, max_price):
        self.name = name
        self.min_price = min_price
        self.max_price = max_price
        self.buy_price = self.min_price
        self.sell_price = self.max_price
        self.quantity = 1
        self.start_time = time.time()
        self.bought_time = time.time() - 14400
        self.unlock_time = time.time()

    def reset_values(self):
        self.buy_price = self.min_price
        self.sell_price = self.max_price
        self.start_time = time.time()

    def reset_start_time(self):
        self.start_time = time.time()

    def set_bought_time(self, time):
        self.bought_time = time
        self.unlock_time = time + 14400

    def __str__(self):
        return "\n===== ITEM =====\nName: " + self.name + "\nMinimum price: " + self.min_price + "\nMaximum price: " + self.max_price + "\nCurrent offer price: " + self.buy_price + "\n===== END OF ITEM =====\n"

class Session(threading.Thread):
    def __init__(self, account, run_event, name=None):
        threading.Thread.__init__(self)
        self.run_event = run_event

        self.id = self.get_new_session_id()
        self.account = account
        self.name = self.account.username

        # Configure logging
        self.configure_logging()

        logging.info('initializing session...')

        self.driver, self.wait = None, None
        self.elements = self.get_elements_json('elements.json')
        self.items = self.get_items_from_file('item_names.txt', 'items_to_flip.txt')
        self.slots = []
        for i in range(0,8):
            self.slots.append(Slot(i, None, None))
        self.last_item = self.items[-1]

        logging.info('initialized session %s' % self.id)

        self.starting_wealth = "0"
        self.current_wealth = "0"
        self.profit = "0"

        self.money_pouch_value = "0"
        
    # This method get's called automaticly after starting the thread
    def run(self):
        money_pouch_css_selector = self.elements["elements"][2]["grand_exchange_page"][0]["slot"][17]["css_selector"]

        logging.info('starting session...')

        try:
            self.driver, self.wait = self.create_webdriver(15)

            self.login(self.account)

            # need to switch between tabs with some time in between to update money pouch value (THIS IS A BUG IN JAGEX's WEBAPPLICATION)
            pouch_is_visible = False
            while pouch_is_visible == False:
                self.open_menu_tab_bank()
                time.sleep(1)
                self.open_menu_tab_grand_exchange()
                time.sleep(.5)
                self.wait.until(lambda driver: driver.find_element_by_css_selector(money_pouch_css_selector))
                element = self.driver.find_element_by_css_selector(money_pouch_css_selector)
                element_attribute = element.get_attribute("innerHTML")
                parsed_value = element_attribute.replace(',', '').replace(' ', '').replace('gp', '')

                try:
                    temp = int(parsed_value) - 0
                    pouch_is_visible = True
                except Exception, e:
                    pass

            # Initialize slot values
            for slot in self.slots:
                self.initialize_slot(slot)

            # Initialize wealth values
            self.calculate_wealth_and_profit()
            self.starting_wealth = self.current_wealth

            # Initialize last item (checks last item from previous session)
            self.initialize_last_item()

            logging.info('starting checks...')
            # Run the initial check (needs to be done to get the right bought-time)
            self.run_checks(15, True)

            # Loop the checks
            while self.run_event.is_set():
                self.run_checks(15, False)

        # except Exception, e:
        #     if not self.internet_on():
        #         logging.warning('no internet connection found')
        #         while not self.internet_on():
        #             logging.warning('waiting for internet conncetion...')
        #             time.sleep(15)
        #         self.reconnect()
        #     else:
        #         logging.error(traceback.format_exc())
        #         self.driver.save_screenshot('screenshots/session_%s-%s.png' % (self.id, datetime.now().strftime("%Y%m%d-%H%M%S")))
        #         # Thread will end here

        except Exception, e:
            logging.error(traceback.format_exc())
            if not self.internet_on():
                logging.warning('no internet connection found')
                while not self.internet_on():
                    logging.warning('waiting for internet conncetion...')
                    time.sleep(15)
                self.reconnect()
            else:
                try:
                    # Initialize css selector for necessary elements
                    # Check if the connection lost modal is shown
                    connection_lost_modal_css_selector = self.elements["elements"][0]["login_form"][6]["css_selector"]
                    element = self.driver.find_element_by_css_selector(connection_lost_modal_css_selector)

                    # If so then this (reconnect) will be called
                    self.reconnect()                    
                except Exception, e:
                    logging.error(traceback.format_exc())
                    self.driver.save_screenshot('screenshots/session_%s-%s.png' % (self.id, datetime.now().strftime("%Y%m%d-%H%M%S")))
                    # Thread will end here
                        
    def configure_logging(self):
        with open(settings_path, 'r') as data_file:
            settings = json.load(data_file)
            loglevel = settings["settings"][0]["value"].upper()

            if (loglevel == "DEBUG" or loglevel == "INFO" or loglevel == "WARNING" or loglevel == "ERROR" or loglevel == "CRITICAL"):
                numeric_level = getattr(logging, loglevel, None)
                if not isinstance(numeric_level, int):
                    raise ValueError('Invalid logging level in settings: %s' % loglevel)
                logging.basicConfig(filename='log/%s.log' % datetime.now().strftime("%Y%m%d-%H%M%S") , format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s', level=numeric_level, datefmt='%m/%d/%Y %I:%M:%S %p')

            else:
                print "Invalid logging level defined in settings. Valid values are: DEBUG, INFO, WARNING, ERROR, CRITICAL"
                self.driver.close()
                sys.exit(1)

    def get_new_session_id(self):
        with open(settings_path, 'r') as data_file:
            settings = json.load(data_file)
            id = int(settings["session_data"][0]["value"]) + 1
            settings["session_data"][0]["value"] = id

        with open(settings_path, 'w') as data_file:
            data_file.write(json.dumps(settings))

        return id

    def internet_on(self):
        try:
            response=urllib2.urlopen('http://google.com',timeout=15)
            return True
        except urllib2.URLError as err:
            pass
        return False

    def reconnect(self):
        # logging.info('waiting for timeout...')
        # time.sleep(30)
        # logging.info('attempting to reconnect...')
        # # Initialize css selector for necessary elements
        # disconnected_popup_ok_css_selector = self.elements["elements"][0]["login_form"][5]["css_selector"]

        # # Click the ok button on the popup
        # self.wait.until(lambda driver: driver.find_element_by_css_selector(disconnected_popup_ok_css_selector))
        # element = self.driver.find_element_by_css_selector(disconnected_popup_ok_css_selector)
        # element.click()

        logging.info('quiting driver...')
        self.driver.quit()

        logging.info('starting new driver..')
        # Restart
        self.run()

    def run_checks(self, interval, initail_check):
        # print "Check started..."
        for slot in self.slots:
            if self.run_event.is_set():
                self.check_slot_state_changed(slot, initail_check)
        self.calculate_wealth_and_profit()
        time.sleep(interval)

    def set_money_pouch_value(self, value):
        self.money_pouch_value = value

    def calculate_wealth_and_profit(self):
        wealth = 0
        for slot in self.slots:
            wealth += int(slot.value)
        wealth += int(self.money_pouch_value)

        self.current_wealth = wealth
        self.profit = wealth - int(self.starting_wealth)

    def initialize_last_item(self):
        # Get last item based on current slots
        # last_item_index = 0
        # for slot in self.slots:
        #     if slot.item != None and self.items.index(slot.item) > last_item_index:
        #         last_item_index = self.items.index(slot.item)

        # self.last_item = self.items[last_item_index]

        # Get last items based on settings file
        with open(settings_path, 'r') as data_file:
            settings = json.load(data_file)
            last_item_name = settings["accounts"][self.account.id]["last_item"].lower()

            # Setting last item to the first item in the list in case the previous last item is no longer in the current item list
            self.last_item = self.items[0]

            for item in self.items:
                if item.name.lower() == last_item_name:
                    self.last_item = self.items[self.items.index(item)]

        logging.info('last flipped item:\t%s' % self.last_item.name)

    def set_last_item(self, item):
        with open(settings_path, 'r') as data_file:
            settings = json.load(data_file)
            settings["accounts"][self.account.id]["last_item"] = item.name

        with open(settings_path, 'w') as data_file:
            data_file.write(json.dumps(settings))

        return item

    def get_next_item(self):
        # reset buy_price and sell_price values of last item
        item = self.items[self.items.index(self.last_item)]
        item.reset_values()

        if len(self.items) == self.items.index(self.last_item) + 1:
            item = self.items[0]
            self.last_item = self.set_last_item(item)

        else:
            item = self.items[self.items.index(self.last_item) + 1]
            self.last_item = self.set_last_item(item)

        return item
        
    def get_items_from_file(self, item_names_url, items_to_flip_url):
        items = []
        item_names_long = []
        item_names_short =[]

        with open(item_names_url) as item_names_data:
            for line in item_names_data:
                item_names_long.append(line.split(':')[0].replace('\n', ''))
                item_names_short.append(line.split(':')[1].replace('\n', ''))

        with open(items_to_flip_url) as data_file:
            for line in data_file:
                if '-' in line:
                    lower_line = line.lower()
                    parsed_line = lower_line.replace('-', ':').replace(' ', '').replace('*', '').replace("\n", "")
                    parsed_line_data = parsed_line.split(':')
                    item_name = parsed_line_data[0]
                    item_min_price = str(int(parsed_line_data[1]) * 1000)
                    item_max_price = str(int(parsed_line_data[2]) * 1000)

                    for item_name_short in item_names_short:
                        if item_name_short == item_name:
                            item_name_long = item_names_long[item_names_short.index(item_name_short)]
                            items.append(Item(item_name_long.lower(), item_min_price, item_max_price))

        return items

    def get_elements_json(self, file_url):
        with open(file_url) as data_file:
            elements = json.load(data_file)

        return elements

    def create_webdriver(self, max_wait_for_element):
        logging.info('creating new webdriver...')

        driver = webdriver.Firefox()
        driver.maximize_window()
        # driver = webdriver.PhantomJS()
        # driver.set_window_size(1920, 1080)
        wait = ui.WebDriverWait(driver, max_wait_for_element)

        # Get a random available world
        world = available_worlds.pop(random.randint(0, len(available_worlds) - 1))

        driver.get('https://secure.runescape.com/m=world%s/html5/comapp/' % str(world))
        wait.until(lambda driver: driver.title.lower().startswith('rs companion'))

        return driver, wait

    def login(self, account):
        logging.info('loging in %s' % account.username)
        # Initialize css selector for necessary elements
        username_css_selector = self.elements["elements"][0]["login_form"][0]["css_selector"]
        password_css_selector = self.elements["elements"][0]["login_form"][1]["css_selector"]
        authenticator_css_selector = self.elements["elements"][0]["login_form"][2]["css_selector"]
        save_password_button_no = self.elements["elements"][0]["login_form"][3]["css_selector"]

        # Wait for login page to load properly by waiting for username element in page
        self.wait.until(lambda driver: driver.find_element_by_css_selector(username_css_selector).is_displayed())
        time.sleep(5)

        # Fill in and submit the login form
        element = self.driver.find_element_by_css_selector(username_css_selector)
        element.send_keys(account.username)

        element = self.driver.find_element_by_css_selector(password_css_selector)
        element.send_keys(account.password)
        element.submit()

        try:
            # Wait for authenticator popup to load properly by waiting for input_field element in page
            self.wait.until(lambda driver: driver.find_element_by_css_selector(authenticator_css_selector))

            # Fill in and submit the authenticator form
            element = self.driver.find_element_by_css_selector(authenticator_css_selector)
            authenticator = Authenticator()
            token = authenticator.get_totp_token(account.authenticator_secret_key)
            time.sleep(5)
            element.send_keys(str(token))
            time.sleep(.5)
            element.submit()
        except Exception, e:
            logging.info('no authenticator confirmation was asked')

        # Click no on Save Password form
        self.wait.until(lambda driver: driver.find_element_by_css_selector(save_password_button_no))
        element = self.driver.find_element_by_css_selector(save_password_button_no)
        element.click()

        logging.info('successfully logged in')

    def logout(self):
        logging.info('logging out...')
        # Initialize css selector for necessary elements
        logout_tab_css_selector = self.elements["elements"][1]["menu_tabs"][8]["css_selector"]
        logout_page_button_ok_css_selector = self.elements["elements"][10]["logout_page"][1]["css_selector"]

        self.wait.until(lambda driver: driver.find_element_by_css_selector(logout_tab_css_selector))
        element = self.driver.find_element_by_css_selector(logout_tab_css_selector)
        element.click()

        self.wait.until(lambda driver: driver.find_element_by_css_selector(logout_page_button_ok_css_selector))
        element = self.driver.find_element_by_css_selector(logout_page_button_ok_css_selector)
        element.click()

        logging.info('successfully logged out')

    def open_menu_tab_grand_exchange(self):
        # Initialize css selector for necessary elements
        grand_exchange_tab_css_selector = self.elements["elements"][1]["menu_tabs"][0]["css_selector"]

        self.wait.until(lambda driver: driver.find_element_by_css_selector(grand_exchange_tab_css_selector))
        element = self.driver.find_element_by_css_selector(grand_exchange_tab_css_selector)
        element.click()

    def open_menu_tab_bank(self):
        # Initialize css selector for necessary elements
        bank_tab_css_selector = self.elements["elements"][1]["menu_tabs"][1]["css_selector"]

        self.wait.until(lambda driver: driver.find_element_by_css_selector(bank_tab_css_selector))
        element = self.driver.find_element_by_css_selector(bank_tab_css_selector)
        element.click()

    def buy(self, slot):
        # Initialize css selector for necessary elements
        buy_button_css_selector = (self.elements["elements"][2]["grand_exchange_page"][0]["slot"][7]["css_selector"]).replace("SLOT_NUMBER", str(slot.get_id() + 1)) # +1 since this html is NOT zero-based index
        buy_search_css_selector = self.elements["elements"][2]["grand_exchange_page"][2]["css_selector"]
        buy_search_result_item_css_selector = (self.elements["elements"][2]["grand_exchange_page"][4]["css_selector"])
        buy_search_result_item_details_item_css_selector = (self.elements["elements"][2]["grand_exchange_page"][6]["css_selector"])

        offer_quantity_css_selector = self.elements["elements"][2]["grand_exchange_page"][9]["css_selector"]
        offer_price_per_item_css_selector = self.elements["elements"][2]["grand_exchange_page"][10]["css_selector"]
        offer_button_confirm_css_selector = self.elements["elements"][2]["grand_exchange_page"][15]["css_selector"]
        offer_button_confirm_button_ok_buy_css_selector = self.elements["elements"][2]["grand_exchange_page"][16]["css_selector"]
        offer_button_confirm_button_cancel_buy_css_selector = self.elements["elements"][2]["grand_exchange_page"][17]["css_selector"]

        # Check if the item in the slot did not buy within the last 4 hours (buy limit)
        if (slot.item.unlock_time == None or slot.item.unlock_time < time.time()):

            # Click on buy button
            self.wait.until(lambda driver: driver.find_element_by_css_selector(buy_button_css_selector))
            element = self.driver.find_element_by_css_selector(buy_button_css_selector)
            element.click()

            # Search item
            self.wait.until(lambda driver: driver.find_element_by_css_selector(buy_search_css_selector))
            element = self.driver.find_element_by_css_selector(buy_search_css_selector)
            element.send_keys(slot.item.name)

            # SOLVE THIS SLEEP #
            time.sleep(7)

            # # Possible solution for the above sleep
            # # Waits for first item in the result list to be visible
            # element = WebDriverWait(self.driver, 10).until(
            #     EC.presence_of_element_located((By.CSS_SELECTOR, buy_search_result_item_css_selector.replace("CHILD_NUMBER", str(1))))
            # )

            # Confirm and select item (search through results)
            # i must start at 1 since the child number is not zero based
            i = 1
            name = None
            while name != slot.item.name:
                self.wait.until(lambda driver: driver.find_element_by_css_selector(buy_search_result_item_css_selector.replace("CHILD_NUMBER", str(i))))
                element_item = self.driver.find_element_by_css_selector(buy_search_result_item_css_selector.replace("CHILD_NUMBER", str(i)))
                element_item_name = self.driver.find_element_by_css_selector(buy_search_result_item_details_item_css_selector.replace("CHILD_NUMBER", str(i)))
                name = element_item_name.get_attribute("innerHTML").lower()

                i += 1
            
            element_item.click()

            # Fill in offer
            # Quantity
            self.wait.until(lambda driver: driver.find_element_by_css_selector(offer_quantity_css_selector))
            element = self.driver.find_element_by_css_selector(offer_quantity_css_selector)
            element.send_keys(slot.item.quantity)

            # Price per item
            self.wait.until(lambda driver: driver.find_element_by_css_selector(offer_price_per_item_css_selector))
            element = self.driver.find_element_by_css_selector(offer_price_per_item_css_selector)
            element.clear()
            element.send_keys(slot.item.buy_price)

            # Safety check on item name


            # Confirm offer
            self.wait.until(lambda driver: driver.find_element_by_css_selector(offer_button_confirm_css_selector))
            element = self.driver.find_element_by_css_selector(offer_button_confirm_css_selector)
            element.click()

            # Confirm popup
            self.wait.until(lambda driver: driver.find_element_by_css_selector(offer_button_confirm_button_ok_buy_css_selector))
            element = self.driver.find_element_by_css_selector(offer_button_confirm_button_ok_buy_css_selector)
            element.click()

            # Go back to Grand Exchange page
            self.open_menu_tab_grand_exchange()

            # Set slot state to buying
            slot.state = Slot_States.BUYING

    def sell(self, slot):
        # Initialize css selector for necessary elements
        sell_button_css_selector = (self.elements["elements"][2]["grand_exchange_page"][0]["slot"][8]["css_selector"]).replace("SLOT_NUMBER", str(slot.get_id() + 1)) # +1 since this html is NOT zero-based index
        bank_search_css_selector = self.elements["elements"][3]["bank_page"][0]["css_selector"]
        bank_search_result_item_css_selector = (self.elements["elements"][3]["bank_page"][1]["css_selector"]).replace("CHILD_NUMBER", "1")
        bank_search_result_item_button_sell_css_selector = self.elements["elements"][3]["bank_page"][4]["css_selector"]

        offer_quantity_css_selector = self.elements["elements"][2]["grand_exchange_page"][9]["css_selector"]
        offer_price_per_item_css_selector = self.elements["elements"][2]["grand_exchange_page"][10]["css_selector"]
        offer_button_confirm_css_selector = self.elements["elements"][2]["grand_exchange_page"][15]["css_selector"]
        offer_button_confirm_button_ok_sell_css_selector = self.elements["elements"][2]["grand_exchange_page"][18]["css_selector"]
        offer_button_confirm_button_cancel_sell_css_selector = self.elements["elements"][2]["grand_exchange_page"][19]["css_selector"]

        # Click on sell button
        self.wait.until(lambda driver: driver.find_element_by_css_selector(sell_button_css_selector))
        element = self.driver.find_element_by_css_selector(sell_button_css_selector)
        element.click()

        # Search item
        self.wait.until(lambda driver: driver.find_element_by_css_selector(bank_search_css_selector))
        element = self.driver.find_element_by_css_selector(bank_search_css_selector)
        element.send_keys(slot.item.name)

        # SOLVE THIS SLEEP #
        time.sleep(2)

        # Select item
        self.wait.until(lambda driver: driver.find_element_by_css_selector(bank_search_result_item_css_selector))
        element = self.driver.find_element_by_css_selector(bank_search_result_item_css_selector)
        element.click()

        # Click Sell button item
        self.wait.until(lambda driver: driver.find_element_by_css_selector(bank_search_result_item_button_sell_css_selector))
        element = self.driver.find_element_by_css_selector(bank_search_result_item_button_sell_css_selector)
        element.click()

        # Fill in offer
        # Quantity
        self.wait.until(lambda driver: driver.find_element_by_css_selector(offer_quantity_css_selector))
        element = self.driver.find_element_by_css_selector(offer_quantity_css_selector)
        element.clear()
        element.send_keys(slot.item.quantity)

        # Price per item
        self.wait.until(lambda driver: driver.find_element_by_css_selector(offer_price_per_item_css_selector))
        element = self.driver.find_element_by_css_selector(offer_price_per_item_css_selector)
        element.clear()
        element.send_keys(slot.item.sell_price)

        # Safety check on item name

        # Confirm offer
        self.wait.until(lambda driver: driver.find_element_by_css_selector(offer_button_confirm_css_selector))
        element = self.driver.find_element_by_css_selector(offer_button_confirm_css_selector)
        element.click()

        # Confirm popup
        self.wait.until(lambda driver: driver.find_element_by_css_selector(offer_button_confirm_button_ok_sell_css_selector))
        element = self.driver.find_element_by_css_selector(offer_button_confirm_button_ok_sell_css_selector)
        element.click()

        # Go back to Grand Exchange page
        self.open_menu_tab_grand_exchange()

        # Set slot state to selling
        slot.state = Slot_States.SELLING

    def abort(self, slot):
        # Initialize css selector for necessary elements
        open_slot_css_selector = (self.elements["elements"][2]["grand_exchange_page"][0]["slot"][15]["css_selector"]).replace("SLOT_NUMBER", str(slot.get_id() + 1)) # +1 since this html is NOT zero-based index
        confirmed_offer_abort_css_selector = self.elements["elements"][2]["grand_exchange_page"][24]["css_selector"]
        confirmed_offer_abort_confirm_button_ok_css_selector = self.elements["elements"][2]["grand_exchange_page"][25]["css_selector"]
        confirmed_offer_abort_confirm_confirmed_ok_css_selector = self.elements["elements"][2]["grand_exchange_page"][27]["css_selector"]

        # Open slot
        self.wait.until(lambda driver: driver.find_element_by_css_selector(open_slot_css_selector))
        element = self.driver.find_element_by_css_selector(open_slot_css_selector)
        element.click()

        # SOLVE THIS SLEEP
        time.sleep(5)

        try:
            # Click abort button
            self.wait.until(lambda driver: driver.find_element_by_css_selector(confirmed_offer_abort_css_selector))
            element = self.driver.find_element_by_css_selector(confirmed_offer_abort_css_selector)
            element.click()

            time.sleep(.5)

            # Click ok on confirmation popup
            self.wait.until(lambda driver: driver.find_element_by_css_selector(confirmed_offer_abort_confirm_button_ok_css_selector))
            element = self.driver.find_element_by_css_selector(confirmed_offer_abort_confirm_button_ok_css_selector)
            element.click()

            time.sleep(.5)

            # Click ok on abort request popup
            self.wait.until(lambda driver: driver.find_element_by_css_selector(confirmed_offer_abort_confirm_confirmed_ok_css_selector))
            element = self.driver.find_element_by_css_selector(confirmed_offer_abort_confirm_confirmed_ok_css_selector)
            element.click()

            # This can be considdered as unnecesay, since it will be changed to empty after the collection happened. But in case there is a DC the slot state needs to be correct at any point in time
            # Set slot state to aborted
            if (slot.state == Slot_States.BUYING):
                slot.state = Slot_States.ABORTED_BUYING
            # Assuming: slot.state == Slot_States.SELLING
            else:
                slot.state = Slot_States.ABORTED_SELLING

        except TimeoutException, e:
            # If it comes here it means the offer completed at the moment it was about to be canceled
            try:
                self.collect(slot, True)
            except Exception, e:
                logging.error(traceback.format_exc())
                self.driver.save_screenshot('screenshots/session_%s-%s.png' % (self.id, datetime.now().strftime("%Y%m%d-%H%M%S")))

    def collect(self, slot, is_opened):
        # Initialize css selector for necessary elements
        open_slot_css_selector = (self.elements["elements"][2]["grand_exchange_page"][0]["slot"][15]["css_selector"]).replace("SLOT_NUMBER", str(slot.get_id() + 1)) # +1 since this html is NOT zero-based index
        confirmed_offer_collect_slot_1_css_selector = self.elements["elements"][2]["grand_exchange_page"][28]["css_selector"]
        confirmed_offer_collect_slot_2_css_selector = self.elements["elements"][2]["grand_exchange_page"][29]["css_selector"]

        # If the slot is not opened yet, we open it here
        if (is_opened == False):
            # Open slot
            # SOLVE THIS SLEEP #
            time.sleep(1)
            self.wait.until(lambda driver: driver.find_element_by_css_selector(open_slot_css_selector))
            element = self.driver.find_element_by_css_selector(open_slot_css_selector)
            element.click()

        try:
            # Collect collection slot 2
            self.wait.until(lambda driver: driver.find_element_by_css_selector(confirmed_offer_collect_slot_2_css_selector))
            element = self.driver.find_element_by_css_selector(confirmed_offer_collect_slot_2_css_selector)
            element.click()
            # print "2 Collection slots where found, collected 2nd slot"

        except Exception, e:
            # print "No 2nd collection slot was found"
            pass

        # Collect collection slot 1
        self.wait.until(lambda driver: driver.find_element_by_css_selector(confirmed_offer_collect_slot_1_css_selector))
        element = self.driver.find_element_by_css_selector(confirmed_offer_collect_slot_1_css_selector)
        element.click()

        # Set slot state to empty
        slot.state = Slot_States.EMPTY

    def check_slot_state_changed(self, slot, initial_check):
        # Initialize css selector for necessary elements
        slot_state_css_selector = (self.elements["elements"][2]["grand_exchange_page"][0]["slot"][16]["css_selector"]).replace("SLOT_NUMBER", str(slot.get_id() + 1)) # +1 since this html is NOT zero-based index
        money_pouch_css_selector = self.elements["elements"][2]["grand_exchange_page"][0]["slot"][17]["css_selector"]
        details_price_css_selector = (self.elements["elements"][2]["grand_exchange_page"][0]["slot"][10]["css_selector"]).replace("SLOT_NUMBER", str(slot.get_id() + 1)) # +1 since this html is NOT zero-based index

        time.sleep(.5)

        self.wait.until(lambda driver: driver.find_element_by_css_selector(slot_state_css_selector))
        element = self.driver.find_element_by_css_selector(slot_state_css_selector)

        element_attribute = element.get_attribute("class")

        if (element_attribute == slot.state.value):
            # print "Slot state remained the same: " + slot.state.value
            if (element_attribute == Slot_States.EMPTY.value):
                # Assign new item to slot
                slot.set_item(self.get_next_item())

                # Buy the new item
                self.buy(slot)

            elif (element_attribute == Slot_States.ABORTED_BUYING.value):
                # Collect the cash
                self.collect(slot, False)

                # Assign new item to slot
                slot.set_item(self.get_next_item())

                # Buy the new item
                self.buy(slot)

            elif (element_attribute == Slot_States.ABORTED_SELLING.value):
                # Collect the item
                self.collect(slot, False)

                # Re-sell the item
                self.sell(slot)

            elif (element_attribute == Slot_States.COMPLETE_BUYING.value):
                logging.info('Finished buying:\t%s\tBuy price: %s\tSell price: N/A' % (slot.item.name, slot.item.buy_price))

                slot.state = Slot_States.COMPLETE_BUYING
                # Collect item
                self.collect(slot, False)

                if not initial_check:
                    slot.item.set_bought_time(time.time())

                # Sell the item
                self.sell(slot)

            elif (element_attribute == Slot_States.COMPLETE_SELLING.value):
                logging.info('Finished selling:\t%s\tBuy price: %s\tSell price: %s' % (slot.item.name, slot.item.buy_price, slot.item.sell_price))

                slot.state = Slot_States.COMPLETE_SELLING

                # Collect cash
                self.collect(slot, False)

                # Assign new item to slot
                slot.set_item(self.get_next_item())

                # Buy the new item
                self.buy(slot)
                
            # Check the time of the slot in this state and this price
            # if over refreshrate (in seconds) -> 900 = 15 minutes
            if (time.time() - slot.item.start_time >= 900):
                # Abort offer
                self.abort(slot)

                # Collect item/cash
                self.collect(slot, True)

                if (element_attribute == Slot_States.BUYING.value):
                    # Increase buy price with 25000 (25k) ONLY IF THIS DOES NOT GO OVER 25000 BELOW MAX PRICE
                    if (int(slot.item.buy_price) + 25000 < int(slot.item.max_price)):
                        if (slot.item.unlock_time == None or slot.item.unlock_time < time.time()):
                            slot.item.buy_price = str(int(slot.item.buy_price) + 25000)

                    elif (slot.item.unlock_time == None or slot.item.unlock_time < time.time()):
                        # If we come here it means the profit is to low and this item will no longer be bought for now
                        # Assign next item to slot
                        slot.set_item(self.get_next_item())

                    # Buy item
                    self.buy(slot)

                    # Reset slot start time
                    slot.item.reset_start_time()

                elif (element_attribute == Slot_States.SELLING.value):
                    # Decrease sell price with 25000 (25k) ONLY IF THIS DOES NOT GO UNDER 25000 ABOVE BUY PRICE (the price it was bought for)
                    if (int(slot.item.sell_price) - 25000 > int(slot.item.buy_price)):
                        slot.item.sell_price = str(int(slot.item.sell_price) - 25000)
                    else:
                        # Sell the item for the same price it bought for
                        slot.item.sell_price = slot.item.buy_price

                    # Sell item
                    self.sell(slot)

                    # Reset slot start time
                    slot.item.reset_start_time()

        else:
            # Assuming: element_attribute == Slot_States.COMPLETE_BUYING.value || element_attribute == Slot_States.COMPLETE_SELLING.valueS
            # Save state in variable since the state will change but we need the current one as well at the state with **MARK**
            previous_state = copy.deepcopy(slot.state)

            if (previous_state == Slot_States.ABORTED_BUYING or previous_state == Slot_States.ABORTED_SELLING):
                self.abort(slot)
                self.collect(True)
            else:
                # Collect the collection slots
                self.collect(slot, False)

            # **MARK**
            if (previous_state == Slot_States.BUYING):
                logging.info('Finished buying:\t%s\tBuy price: %s\tSell price: N/A' % (slot.item.name, slot.item.buy_price))

                # Slot state went from buying -> complete, so bought item needs to be sold back
                # Set time bought to current time
                slot.item.set_bought_time(time.time())

                # Sell the collected item if state was buying
                self.sell(slot)

                # Reset slot start time
                slot.item.reset_start_time()

            else:
                logging.info('Finished selling:\t%s\tBuy price: %s\tSell price: %s' % (slot.item.name, slot.item.buy_price, slot.item.sell_price))

                # Slot state went from selling -> complete, so new item needs to be bought
                # Assign new item to slot
                slot.set_item(self.get_next_item())

                # Buy the new item
                self.buy(slot)

                # Reset slot start time
                slot.item.reset_start_time()

        # Calculate current wealth
        self.wait.until(lambda driver: driver.find_element_by_css_selector(money_pouch_css_selector))
        element = self.driver.find_element_by_css_selector(money_pouch_css_selector)

        element_attribute = element.get_attribute("innerHTML")
        money_pouch_value = element_attribute.replace(',', '').replace(' ', '').replace('gp', '')

        self.set_money_pouch_value(money_pouch_value)

        self.wait.until(lambda driver: driver.find_element_by_css_selector(details_price_css_selector))
        element = self.driver.find_element_by_css_selector(details_price_css_selector)

        element_attribute = element.get_attribute("innerHTML")
        slot_value = element_attribute.replace(',', '').replace(' ', '').replace('gp', '')

        slot.set_value(slot_value)

    def initialize_slot(self, slot):
        # Initialize css selector for necessary elements
        slot_state_css_selector = (self.elements["elements"][2]["grand_exchange_page"][0]["slot"][16]["css_selector"]).replace("SLOT_NUMBER", str(slot.get_id() + 1)) # +1 since this html is NOT zero-based index

        # Get the element containing the current state of the slot
        self.wait.until(lambda driver: driver.find_element_by_css_selector(slot_state_css_selector))
        element = self.driver.find_element_by_css_selector(slot_state_css_selector)

        # Get the actual slot state value
        element_attribute = element.get_attribute("class")

        # Set the slot state to the correct state and add the right item to the slot
        if (element_attribute == Slot_States.COMPLETE_BUYING.value):
            slot.state = Slot_States.COMPLETE_BUYING

            # Initialize the item
            self.initialize_slot_for_buy(slot)

            # # Sell the item
            # self.sell(slot)

        elif (element_attribute == Slot_States.COMPLETE_SELLING.value):
            slot.state = Slot_States.COMPLETE_SELLING

             # Initialize the item
            self.initialize_slot_for_sell(slot)

            # # Assign new item to slot
            # slot.set_item(self.get_next_item())

            # # Buy the new item
            # self.buy(slot)

        elif (element_attribute == Slot_States.ABORTED_BUYING.value):
            slot.state = Slot_States.ABORTED_BUYING

            # Initialize the item
            self.initialize_slot_for_buy(slot)

        elif (element_attribute == Slot_States.ABORTED_SELLING.value):
            slot.state = Slot_States.ABORTED_SELLING

            # Initialize the item
            self.initialize_slot_for_sell(slot)

        elif (element_attribute == Slot_States.BUYING.value):
            slot.state = Slot_States.BUYING

            # Initialize the item
            self.initialize_slot_for_buy(slot)

        elif (element_attribute == Slot_States.SELLING.value):
            slot.state = Slot_States.SELLING

            # Initialize the item
            self.initialize_slot_for_sell(slot)

        elif (element_attribute == Slot_States.EMPTY.value):
            slot.state = Slot_States.EMPTY
            slot.set_item(None)

    def initialize_slot_for_sell(self, slot):
        # Initialize css selector for necessary elements
        details_item_css_selector = (self.elements["elements"][2]["grand_exchange_page"][0]["slot"][9]["css_selector"]).replace("SLOT_NUMBER", str(slot.get_id() + 1)) # +1 since this html is NOT zero-based index
        money_pouch_css_selector = self.elements["elements"][2]["grand_exchange_page"][0]["slot"][17]["css_selector"]
        details_price_css_selector = (self.elements["elements"][2]["grand_exchange_page"][0]["slot"][10]["css_selector"]).replace("SLOT_NUMBER", str(slot.get_id() + 1)) # +1 since this html is NOT zero-based index


        # Get the element containing the item name that's in the slot
        self.wait.until(lambda driver: driver.find_element_by_css_selector(details_item_css_selector))
        element = self.driver.find_element_by_css_selector(details_item_css_selector)

        # Get the actual element value (item name)
        element_value = element.get_attribute("innerHTML")

        for item in self.items:
            if item.name.lower() == element_value.lower():
                # Assign the correct item to this slot
                slot.set_item(item)

        # Get the element containing the sell price
        self.wait.until(lambda driver: driver.find_element_by_css_selector(details_price_css_selector))
        element = self.driver.find_element_by_css_selector(details_price_css_selector)

        # Get the actual element value (item name)
        element_value = element.get_attribute("innerHTML")

        # Parse and assign the sell price to the item
        # OLD METHOD
        # parsed_price_array = re.findall(r'\d+', element_value)
        # parsed_price = ""
        # for number in parsed_price_array:
        #     parsed_price += number

        # NEW METHOD
        slot.item.sell_price = element_value.replace(',', '').replace(' ', '').replace('gp', '')

        # Set the slot value
        slot.set_value(slot.item.sell_price)

        # Set the money pouch value
        self.wait.until(lambda driver: driver.find_element_by_css_selector(money_pouch_css_selector))
        element = self.driver.find_element_by_css_selector(money_pouch_css_selector)

        element_attribute = element.get_attribute("innerHTML")
        money_pouch_value = element_attribute.replace(',', '').replace(' ', '').replace('gp', '')

        self.set_money_pouch_value(money_pouch_value)

    def initialize_slot_for_buy(self, slot):
        # Initialize css selector for necessary elements
        details_item_css_selector = (self.elements["elements"][2]["grand_exchange_page"][0]["slot"][9]["css_selector"]).replace("SLOT_NUMBER", str(slot.get_id() + 1)) # +1 since this html is NOT zero-based index
        money_pouch_css_selector = self.elements["elements"][2]["grand_exchange_page"][0]["slot"][17]["css_selector"]
        details_price_css_selector = (self.elements["elements"][2]["grand_exchange_page"][0]["slot"][10]["css_selector"]).replace("SLOT_NUMBER", str(slot.get_id() + 1)) # +1 since this html is NOT zero-based index

        # Get the element containing the item name that's in the slot
        self.wait.until(lambda driver: driver.find_element_by_css_selector(details_item_css_selector))
        element = self.driver.find_element_by_css_selector(details_item_css_selector)

        # Get the actual element value (item name)
        element_value = element.get_attribute("innerHTML")

        for item in self.items:
            if item.name.lower() == element_value.lower():
                # Assign the correct item to this slot
                slot.set_item(item)

        # Get the element containing the buy price
        self.wait.until(lambda driver: driver.find_element_by_css_selector(details_price_css_selector))
        element = self.driver.find_element_by_css_selector(details_price_css_selector)

        # Get the actual element value (item name)
        element_value = element.get_attribute("innerHTML")

        # Parse and assign the buy price to the item
        # OLD METHOD
        # parsed_price_array = re.findall(r'\d+', element_value)
        # parsed_price = ""
        # for number in parsed_price_array:
        #     parsed_price += number

        # NEW METHOD
        slot.item.buy_price = element_value.replace(',', '').replace(' ', '').replace('gp', '')

        # Set the slot value
        slot.set_value(slot.item.buy_price)

        # Set the money pouch value
        self.wait.until(lambda driver: driver.find_element_by_css_selector(money_pouch_css_selector))
        element = self.driver.find_element_by_css_selector(money_pouch_css_selector)

        element_attribute = element.get_attribute("innerHTML")
        money_pouch_value = element_attribute.replace(',', '').replace(' ', '').replace('gp', '')

        self.set_money_pouch_value(money_pouch_value)


def print_session_values(total_starting_wealth, total_current_wealth, total_profit, total_cash_value, total_item_value):
    print "\t\t\t\t\t\t\t\t\tFlipper - By Sacha Van den Wyngaert"
    print '-' * 175
    print "Total starting wealth: %s\nTotal current wealth: %s\nTotal profit: %s\nTotal cash value: %s\nTotal item value: %s\n\n\n" % (total_starting_wealth, total_current_wealth, total_profit, total_cash_value, total_item_value)
    print '-' * 175

def print_menu():
    output = "You can choose one of the following actions while the sessions are running:\n[1] Refresh session values\n[2] Exit\n\n>>>"
    print output

def clear():
    # Clear Windows command prompt.
    if (os.name in ('ce', 'nt', 'dos')):
        os.system('cls')

    # Clear the Linux terminal.
    elif ('posix' in os.name):
        os.system('clear')

def exit():
    print "Attempting to close threads..."
    run_event.clear()
    for session in sessions:
        session.join()
    print "Threads successfully closed!"
    sys.exit()

def get_session_values():
    # Get the necessary values of each session
    total_starting_wealth = 0
    total_current_wealth = 0
    total_profit = 0
    total_cash_value = 0
    total_item_value = 0

    for session in sessions:
        total_starting_wealth += int(session.starting_wealth)
        total_current_wealth += int(session.current_wealth)
        total_profit += int(session.profit)
        total_cash_value += int(session.money_pouch_value)
        total_item_value += (total_current_wealth - total_cash_value)

    return total_starting_wealth, total_current_wealth, total_profit, total_cash_value, total_item_value

if __name__ == "__main__":
    run_event = threading.Event()
    run_event.set()

    sessions = []
    accounts_json = Account.get_accounts_from_json()

    i = 0
    for account in accounts_json:
        sessions.append(Session(Account(account['username'], account['password'], account['authenticator_secret_key'], i), run_event))
        i+=1

    for session in sessions:
        session.deamon = True
        session.start()

        # USE THIS BLOCK IF YOU WANT THE MAIN THREAD TO LISTEN TO NOTHING BUT KEYBOARD INTERUPT (CTRL + C)
    # try:
    #     while 1:
    #         time.sleep(.1)
    # except KeyboardInterrupt:
    #     print "attempting to close threads..."
    #     run_event.clear()
    #     for session in sessions:
    #         session.join()
    #     print "threads successfully closed"
    # ENDBLOCK

    # Wait a while so all sessions can calculate their values -> a signal from the thread would be nice to resolve this sleep...
    time.sleep(60)

    # Get the necessary values of each session
    total_starting_wealth, total_current_wealth, total_profit, total_cash_value, total_item_value = get_session_values()

    clear()
    print_session_values(total_starting_wealth, total_current_wealth, total_profit, total_cash_value, total_item_value)
    print_menu()

    while True:
        action = raw_input()

        if action == '1':
            clear()
            total_starting_wealth, total_current_wealth, total_profit, total_cash_value, total_item_value = get_session_values()
            print_session_values(total_starting_wealth, total_current_wealth, total_profit, total_cash_value, total_item_value)
            print_menu()
        elif action == '2':
            exit()
        else:
            print "'%s' is not a valid option" % str(action)