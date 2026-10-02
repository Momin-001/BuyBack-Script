import scrapy
import json
import csv
import os
import urllib.error
import urllib.request
from itertools import product
from pathlib import Path
from urllib.parse import urljoin, urlsplit

class CellScraperSpider(scrapy.Spider):
    name = "cell_scraper"
    # Two runs, chosen with `-a mode=...`, each pricing the products the
    # dashboard assigns to it (products.price_source on goselldevices):
    #   regular (default) - every catalogue product except those set to Custom
    #                       script or Manual.
    #   custom            - only the products set to Custom script, with their own
    #                       price formula (calculate_custom_price).
    # Manual products are visited by neither.
    modes = ('regular', 'custom')
    # The POOR/FAULTY price rule compares two rows that arrive on separate async
    # requests, so we can't stream to Scrapy's FEED exporter row-by-row. Instead we
    # buffer every row and write the CSV ourselves in closed(), once all requests are
    # done and the cross-condition comparison can be made.
    output_files = {'regular': Path('Outputs/sell_cell.csv'),
                    'custom': Path('Outputs/sell_cell_custom.csv')}
    # Macs add Processor/Memory, which phones and tablets do not have. A fixed field
    # list keeps the header stable no matter which device type is scraped first.
    # Url is the product's SellCell page; the dashboard import saves it on the product.
    fieldnames = ['Category', 'Brand', 'Name', 'Model_Number', 'Image', 'Url', 'Network',
                  'Capacity', 'Memory', 'Processor', 'Condition', 'Price',
                  'Buybackworld_Price', 'Maximum_Price']
    # SellCell's condition labels, renamed for our output only (the site's payload
    # still uses the raw new/working/poor/broken values).
    condition_labels = {'MINT': 'EXCELLENT', 'FAULTY': 'BROKEN'}
    urls = []
    search_urls = {
                   "Phones":["https://www.sellcell.com/sell-iphone/","https://www.sellcell.com/sell/samsung-phone/"],
                   "Tablets":["https://www.sellcell.com/sell/ipad/","https://www.sellcell.com/sell/samsung-tablet/"],
                   "Laptops":["https://www.sellcell.com/sell/apple-macbook/"], 
                   "Smartwatches":["https://www.sellcell.com/sell/apple-watch/"]
                   }
    headers = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'en-US,en;q=0.9',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'Pragma': 'no-cache',
        'Referer': 'https://www.sellcell.com/',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
        'sec-ch-ua': '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'Cookie': '_gcl_au=1.1.1233498191.1775472965; _gid=GA1.2.1480207559.1775472965; _fbp=fb.1.1775472967941.310482786587868967; _gat_gtag_UA_19979388_1=1; _uetsid=3706e83031a711f1b1914521f97c6ba4; _uetvid=3707d01031a711f1b0031d4aa86d4528; _ga_LQT1T7TBSN=GS2.1.s1775474987$o2$g1$t1775474989$j58$l0$h0; _ga=GA1.1.575438080.1775472965'
    }
    model_file = Path(__file__).resolve().parents[1] / 'model_numbers.json'
    site_url = 'https://www.sellcell.com'

    def __init__(self, mode='regular', *args, **kwargs):
        super().__init__(*args, **kwargs)
        if mode not in self.modes:
            raise ValueError(f"mode must be one of {', '.join(self.modes)} (got {mode!r})")
        self.mode = mode
        self.output_file = self.output_files[mode]
        with open(self.model_file, encoding='utf-8') as f:
            categories = json.load(f)
        self.model_map = {}
        self.unmapped_names = set()
        self.items = []
        for devices in categories.values():
            for device_name, model_number in devices.items():
                self.model_map[self.normalize_name(device_name)] = model_number
        self.load_price_sources()

    @staticmethod
    def normalize_name(name):
        return ' '.join(name.split()).casefold()

    @staticmethod
    def normalize_url(url):
        # Same rule as normalizeSourceUrl (goselldevices db/schema/price-source.mjs):
        # the path only, lowercased, with a trailing slash.
        path = urlsplit(url or '').path.lower().rstrip('/')
        return f'{path}/' if path else ''

    def load_price_sources(self):
        # Which products each run visits comes from the analytics dashboard. A run
        # that cannot load it stops here: scraping blind would send custom and
        # manual products to the regular import.
        api_url = os.getenv('SCRAPER_API_URL')
        secret = os.getenv('SCRAPER_API_SECRET')
        if not api_url or not secret:
            raise ValueError('SCRAPER_API_URL and SCRAPER_API_SECRET must be set in .env '
                             '(see .env.example)')
        request = urllib.request.Request(api_url, headers={
            'Authorization': f'Bearer {secret}',
            'Accept': 'application/json',
        })
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                products = json.load(response)['data']['products']
        except urllib.error.HTTPError as e:
            hint = (' - check SCRAPER_API_SECRET matches the dashboard' if e.code == 401
                    else f' - check SCRAPER_API_URL ({api_url})')
            raise RuntimeError(f'Dashboard refused the product list (HTTP {e.code}){hint}') from e
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            raise RuntimeError(f'Could not load the product list from {api_url}: {e}') from e

        not_scraped = [p for p in products if p['priceSource'] != 'scraped']
        custom = [p for p in products if p['priceSource'] == 'custom']
        # Regular run: skip a product by its saved link, or by its name when it has no
        # link yet (a catalogue link's text is the product's name as SellCell's API
        # returns it, which is the name the import stored).
        self.skip_urls = {p['sourceUrl'] for p in not_scraped if p['sourceUrl']}
        self.skip_names = {self.normalize_name(p['name']) for p in not_scraped}
        self.skipped = set()
        # Custom run: a product with a link is requested directly; one without is
        # looked up by name on the catalogue pages.
        self.custom_products = {p['id']: p for p in custom}
        self.custom_with_url = [p for p in custom if p['sourceUrl']]
        self.custom_by_name = {self.normalize_name(p['name']): p for p in custom if not p['sourceUrl']}
        self.priced_custom = set()
        self.logger.info('Loaded %d products from the dashboard: %d custom, %d manual',
                         len(products), len(custom), len(not_scraped) - len(custom))

    def get_model_number(self, name):
        key = self.normalize_name(name)
        if key not in self.model_map:
            # Every variant of a device hits this, so warn once per device name.
            if name and key not in self.unmapped_names:
                self.unmapped_names.add(key)
                self.logger.warning('No model number mapping for device: %s', name)
            return ''
        return self.model_map[key]

    async def start(self):
        if self.mode == 'custom':
            if self.custom_price_formula_missing:
                self.logger.warning('The custom price formula is not set yet - custom products '
                                    'are priced with the regular formula (calculate_custom_price)')
            for p in self.custom_with_url:
                yield self.detail_request(urljoin(self.site_url, p['sourceUrl']), p['category'], p['id'])
            # Catalogue pages are only needed to find custom products by name.
            if not self.custom_by_name:
                return
        for key, urls in self.search_urls.items():
            for url in urls:
                yield scrapy.Request(
                url=url,
                headers=self.headers,
                callback=self.parse,
                meta={"cat": key},
            )


    def parse(self, response, **kwargs):
        anchors = response.css('.device h4 a') or response.css('.devices .h4') or response.css('.devices a')
        collapse = response.xpath('//p[@class="devices-expand-buttons"]/following-sibling::div')
        if collapse:
            anchors = self.get_collapse(collapse) + list(anchors)
        # href -> the link's text, which is the product's name.
        links = {}
        for anchor in anchors:
            href = anchor.attrib.get('href')
            if not href:
                continue
            name = ' '.join(''.join(anchor.css('::text').getall()).split())
            # '.devices a' also matches each product's image link, which has no text.
            if name or href not in links:
                links[href] = name
        for href, name in links.items():
            self.urls.append(href)
            name_key = self.normalize_name(name)
            if self.mode == 'custom':
                target = self.custom_by_name.pop(name_key, None)
                if target:
                    yield self.detail_request(response.urljoin(href), target['category'], target['id'])
                continue
            if self.normalize_url(href) in self.skip_urls or name_key in self.skip_names:
                self.skipped.add(name or href)
                continue
            yield self.detail_request(response.urljoin(href), response.meta['cat'])

    def detail_request(self, url, category, product_id=None):
        # The price lookup's device_name is the last segment of the product's path.
        title = urlsplit(url).path.rstrip('/').split('/')[-1]
        return scrapy.Request(url=url, headers=self.headers, callback=self.parse_details,
                              meta={'cat': category, 'title': title, 'product_id': product_id},
                              dont_filter=True)


    def parse_details(self, response):
        images = response.xpath('//meta[@property="og:image"]/@content').get('')
        networks = response.css('.attribute_network_search') or response.css('input[name="network"]')
        np = True
        if not networks:
            networks = ['No Network']
            np = False
        capacities = response.css('.capacity_options .attribute_capacity_search')
        cp = True
        if not capacities:
            capacities=['No Capacity']
            cp = False
        # Macs label the capacity attribute MEMORY (it is RAM, not storage), so the
        # value belongs in its own column rather than under Capacity.
        capacity_label = ' '.join(response.css('.capacity_label ::text').getall()).strip().casefold()
        is_memory = 'memory' in capacity_label
        # Only Macs carry a processor attribute. The mobile block renders it as a
        # <select>, so this desktop-only class matches each option exactly once.
        processors = response.css('.attribute_processor_search')
        pr = True
        if not processors:
            processors = ['No Processor']
            pr = False
        cn = True
        conditions = response.css('.condition')
        if not conditions:
            conditions = ['No Condition']
            cn = False
        t = response.meta['title'].replace('apple-', '').strip().replace('samsung-','').strip()
        for network, processor, capacity, condition in product(networks, processors, capacities, conditions):
            network_value = network.css('::attr(value)').get('') if np else ''
            # network_value='29'
            # title='galaxy-s10-plus'
            capacity_value = capacity.css('::attr(value)').get('') if cp else ''
            # capacity_value='28'
            processor_value = processor.css('::attr(value)').get('') if pr else ''
            # processor_value='554'
            condition_value = condition.css('input::attr(value)').get('') if cn else ''
            # condition_value='broken'
            attribute_text = capacity.css('::attr(data-attribute)').get('').strip() if cp else ''
            # attribute_text='1TB' for phones/tablets, '64GB' of RAM for Macs
            url = "https://www.sellcell.com/devices/ajax_comparison/"

            payload = f"device_name={t}&attributes%5Bnetwork%5D={network_value}&attributes%5Bcondition%5D={condition_value}&attributes%5Bcapacity%5D={capacity_value}"
            # Omitting this on a Mac makes the site fall back to the
            # highest-priced processor instead of the one we asked for.
            if pr:
                payload += f"&attributes%5Bprocessor%5D={processor_value}"
            payload += "&sort=best_match"
            headers = {
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Accept-Language': 'en-US,en;q=0.9',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'Origin': 'https://www.sellcell.com',
                'Pragma': 'no-cache',
                'Referer': response.url,
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-origin',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
                'X-Requested-With': 'XMLHttpRequest',
                'sec-ch-ua': '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
                'Cookie': '_gcl_au=1.1.1233498191.1775472965; _gid=GA1.2.1480207559.1775472965; _fbp=fb.1.1775472967941.310482786587868967; _ga_LQT1T7TBSN=GS2.1.s1775474987$o2$g1$t1775475250$j56$l0$h0; _ga=GA1.1.575438080.1775472965; _uetsid=3706e83031a711f1b1914521f97c6ba4; _uetvid=3707d01031a711f1b0031d4aa86d4528'
            }
            yield response.follow(url=url, method="POST", headers=headers, body=payload,
                                  callback=self.parse_data,
                                  meta={'cat':response.meta['cat'],
                                        'network': network.css('::attr(data-attribute)').get('').strip() if np else '',
                                        'capacity': '' if is_memory else attribute_text,
                                        'memory': attribute_text if is_memory else '',
                                        'processor': processor.css('::attr(data-attribute)').get('').strip() if pr else '',
                                        'condition': ''.join(condition.css('::text').getall()).strip() if cn else '',
                                        'title':t,
                                        'product_id':response.meta.get('product_id'),
                                        'n':network_value,
                                        'image':images,
                                        'c':condition_value,
                                        'p':processor_value,
                                        'cap':capacity_value,'url':response.url},dont_filter=True)

    def parse_data(self, response):
        try:
            data = json.loads(response.text)
        except Exception as e:
            data = {}

        item = dict()
        buyback_price = self.get_buyback(data.get('prices', []))
        item['Category'] = response.meta['cat']
        item['Brand'] = data.get('brand', '')
        item['Name'] = data.get('name', '')
        item['Model_Number'] = self.get_model_number(item['Name'])
        item['Image'] = response.meta['image']
        item['Url'] = self.normalize_url(response.meta['url'])
        item['Network'] = response.meta['network']
        item['Capacity'] = response.meta['capacity']
        item['Memory'] = response.meta['memory']
        item['Processor'] = response.meta['processor']
        condition = response.meta['condition']
        item['Condition'] = self.condition_labels.get(condition, condition)
        item['Buybackworld_Price'] = buyback_price if buyback_price is not None else 0
        item['Maximum_Price'] = self.get_max(data.get('prices', []))
        # Raw BuyBackWorld price (None when missing) kept for the POOR/FAULTY rule and
        # the final Price calculation, both applied in closed(); not a CSV column.
        item['_buyback'] = buyback_price
        self.items.append(item)
        if response.meta.get('product_id'):
            self.priced_custom.add(response.meta['product_id'])

    def closed(self, reason):
        # All requests are done; now the buffered rows can be cross-compared and priced.
        self.apply_condition_pricing()
        self.write_csv()
        if self.mode == 'regular':
            self.logger.info('Skipped %d products set to Custom script or Manual', len(self.skipped))
            return
        missing = [p['name'] for pid, p in self.custom_products.items() if pid not in self.priced_custom]
        self.logger.info('Priced %d of %d custom products',
                         len(self.custom_products) - len(missing), len(self.custom_products))
        if missing:
            # No saved link and no catalogue link with this name, or a dead link.
            # Paste the product's SellCell link in the goselldevices product form.
            self.logger.warning('Custom products not found on SellCell: %s', ', '.join(sorted(missing)))

    def apply_condition_pricing(self):
        # Requirement 2: within a variant (same product and every attribute except
        # condition), if the BuyBackWorld POOR and FAULTY/BROKEN prices are equal, drop
        # the BROKEN price to 0.6x the POOR price. Runs before Price is calculated so
        # the adjusted value feeds the formula.
        groups = {}
        for item in self.items:
            key = (item['Name'], item['Network'], item['Capacity'],
                   item['Memory'], item['Processor'])
            groups.setdefault(key, {})[item['Condition']] = item
        for variant in groups.values():
            poor = variant.get('POOR')
            broken = variant.get('BROKEN')
            if poor and broken and poor['_buyback'] is not None \
                    and poor['_buyback'] == broken['_buyback']:
                adjusted = round(0.6 * poor['_buyback'], 2)
                broken['_buyback'] = adjusted
                broken['Buybackworld_Price'] = adjusted

        for item in self.items:
            if self.mode == 'custom':
                item['Price'] = self.calculate_custom_price(item)
            else:
                item['Price'] = self.regular_price(item)

    def regular_price(self, item):
        # Requirement 3: SellCell has no BuyBackWorld price for MacBooks, so their Price
        # is 1.02x the maximum price. Every other device keeps the BuyBackWorld tier
        # formula, now applied to the possibly-adjusted BROKEN price.
        if item['Category'] == 'Laptops' and item['Brand'] == 'Apple':
            return round(1.02 * item['Maximum_Price'], 2)
        return self.calculate_price(item['_buyback'])

    # Set to False once calculate_custom_price has its own formula.
    custom_price_formula_missing = True

    def calculate_custom_price(self, item):
        # TODO: the custom script's own price formula (not decided yet). The item
        # carries Maximum_Price, Buybackworld_Price and the raw '_buyback' (None when
        # BuyBackWorld has no offer). Until then custom products are priced exactly
        # like the regular run.
        return self.regular_price(item)

    def write_csv(self):
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_file, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=self.fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(self.items)
        self.logger.info('Wrote %d rows to %s', len(self.items), self.output_file)

    def get_buyback(self, prices):
        for price in prices:
            name = price.get('merchant_full_name', '')
            if name == 'BuyBackWorld':
                return float(price.get('price', ''))

    def calculate_price(self, buyback_price):
        if buyback_price is None:
            return 0
        if buyback_price < 25:
            return buyback_price + 1
        if buyback_price < 50:
            return buyback_price + 2
        if buyback_price < 100:
            return buyback_price + 3
        if buyback_price <= 200:
            return buyback_price + 5
        if buyback_price <= 300:
            return buyback_price + 7
        if buyback_price <= 500:
            return buyback_price + 10
        return buyback_price + 15

    def get_max(self, items):
        prices = [float(item.get('price', '')) for item in items]
        return max(prices) if prices else 0

    def get_collapse(self, data):
        # The product links hidden behind the catalogue's "show more" buttons.
        anchors = []
        for info in data:
            anchors.extend(info.css('a.h4'))
        return anchors



    def get_data(self):
        names = []
        for info in self.data:
            n = info.get('Name', '')
            if n not in names:
                names.append(n)
        return names
