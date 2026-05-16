import scrapy
import json
import csv

class CellScraperSpider(scrapy.Spider):
    name = "cell_scraper"
    custom_settings = {
        'FEED_FORMAT': 'csv',
        'FEED_URI': 'output/sell_cell_tab_glax.csv'
    }
    urls = []
    search_urls = {"Smartphones":["https://www.sellcell.com/sell-iphone/","https://www.sellcell.com/sell/samsung-phone/"],
                   "Tablets":["https://www.sellcell.com/sell/ipad/","https://www.sellcell.com/sell/samsung-tablet/"]}
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

    def start_requests(self):
        for key,urls in self.search_urls.items():
            for url in urls:
                yield scrapy.Request(url=url, headers=self.headers, callback=self.parse,meta={'cat':key})


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
        cn = True
        conditions = response.css('.condition')
        if not conditions:
            conditions = ['No Condition']
            cn = False
        t = response.meta['title'].replace('apple-', '').strip().replace('samsung-','').strip()
        for network in networks:
            for capacity in capacities:
                for condition in conditions:
                    network_value = network.css('::attr(value)').get('') if np else ''
                    # network_value='29'
                    # title='galaxy-s10-plus'
                    capacity_value = capacity.css('::attr(value)').get('') if cp else ''
                    # capacity_value='28'
                    condition_value = condition.css('input::attr(value)').get('') if cn else ''
                    # condition_value='broken'
                    url = "https://www.sellcell.com/devices/ajax_comparison/"

                    payload = f"device_name={t}&attributes%5Bnetwork%5D={network_value}&attributes%5Bcondition%5D={condition_value}&attributes%5Bcapacity%5D={capacity_value}&sort=best_match"
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
                                                'capacity': capacity.css('::attr(data-attribute)').get('').strip() if cp else '',
                                                'condition': ''.join(condition.css('::text').getall()).strip() if cn else '',
                                                'title':t,
                                                'n':network_value,
                                                'image':images,
                                                'c':condition_value,
                                                'cap':capacity_value,'url':response.url},dont_filter=True)

    def parse_data(self, response):
        try:
            data = json.loads(response.text)
        except Exception as e:
            data = {}

        item = dict()
        # item['Category'] = response.meta['cat']
        item['Type'] = data.get('type', '')
        item['Brand'] = data.get('brand', '')
        item['Name'] = data.get('name', '')
        item['Image'] = response.meta['image']
        item['Maximum_Price'] = self.get_max(data.get('prices', []))
        item['Buybackworld_Price'] = self.get_buyback(data.get('prices', []))
        item['Status']='Active'
        item['Network'] = response.meta['network']
        item['Capacity'] = response.meta['capacity']
        item['Condition'] = response.meta['condition']
        yield item

    def get_buyback(self, prices):
        for price in prices:
            name = price.get('merchant_full_name', '')
            if name == 'BuyBackWorld':
                return float(price.get('price', ''))

    def get_max(self, items):
        prices = [float(item.get('price', '')) for item in items]
        return max(prices) if prices else ''

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
