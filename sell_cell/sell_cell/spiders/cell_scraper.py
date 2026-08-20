import scrapy
import json
import csv
from itertools import product
from pathlib import Path

class CellScraperSpider(scrapy.Spider):
    name = "cell_scraper"
    custom_settings = {
        'FEED_FORMAT': 'csv',
        'FEED_URI': 'Outputs/sell_cell.csv',
        # Macs add Processor/Memory, which phones and tablets do not have. Without a
        # fixed field list the CSV header is locked in from whichever item is scraped
        # first and the extra columns would be silently dropped.
        'FEED_EXPORT_FIELDS': ['Category', 'Brand', 'Name', 'Model_Number', 'Image', 'Network',
                               'Capacity', 'Memory', 'Processor', 'Condition', 'Price',
                               'Buybackworld_Price', 'Maximum_Price'],
    }
    urls = []
    search_urls = {
                   "Smartphones":["https://www.sellcell.com/sell-iphone/","https://www.sellcell.com/sell/samsung-phone/"],
                   "Tablets":["https://www.sellcell.com/sell/ipad/","https://www.sellcell.com/sell/samsung-tablet/"],
                   "Laptops":["https://www.sellcell.com/sell/apple-macbook/"]
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with open(self.model_file, encoding='utf-8') as f:
            categories = json.load(f)
        self.model_map = {}
        self.unmapped_names = set()
        for devices in categories.values():
            for device_name, model_number in devices.items():
                self.model_map[self.normalize_name(device_name)] = model_number

    @staticmethod
    def normalize_name(name):
        return ' '.join(name.split()).casefold()

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
        for key, urls in self.search_urls.items():
            for url in urls:
                yield scrapy.Request(
                url=url,
                headers=self.headers,
                callback=self.parse,
                meta={"cat": key},
            )


    def parse(self, response, **kwargs):
        all_results = response.css('.device h4 a::attr(href)').getall() or response.css('.devices .h4::attr(href)').getall() or response.css(
            '.devices a::attr(href)').getall()
        collapse = response.xpath('//p[@class="devices-expand-buttons"]/following-sibling::div')
        urls = []
        if collapse:
            urls = self.get_collapse(collapse)
        urls.extend(all_results)
        urls = list(set(urls))
        for result in urls:
            # if result not in self.urls:
                self.urls.append(result)
                result_url = response.urljoin(result)
                if result:
                    title = result.split('/')[-2]
                    yield response.follow(url=result_url, headers=self.headers, callback=self.parse_details,
                                          meta={'cat':response.meta['cat'],'title':title},dont_filter=True)


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
        item['Network'] = response.meta['network']
        item['Capacity'] = response.meta['capacity']
        item['Memory'] = response.meta['memory']
        item['Processor'] = response.meta['processor']
        item['Condition'] = response.meta['condition']
        item['Price'] = self.calculate_price(buyback_price)
        item['Buybackworld_Price'] = buyback_price if buyback_price is not None else 0
        item['Maximum_Price'] = self.get_max(data.get('prices', []))
        yield item

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
        urls =[]
        for info in data:
            data_urls = info.css('a.h4::attr(href)').getall()
            for url in data_urls:
                    urls.append(url)

        return urls



    def get_data(self):
        names = []
        for info in self.data:
            n = info.get('Name', '')
            if n not in names:
                names.append(n)
        return names
