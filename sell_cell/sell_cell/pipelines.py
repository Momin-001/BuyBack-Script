import os
import re
import uuid
import psycopg2
from slugify import slugify
from datetime import datetime


class SellCellPipeline:

    def open_spider(self, spider):

        db_name = "sellcell"

        # 1️⃣ Connect to default postgres database
        temp_conn = psycopg2.connect(
            host=os.getenv("PG_HOST", "localhost"),
            database="postgres",  # connect to existing DB first
            user=os.getenv("PG_USER", "postgres"),
            password=os.getenv("PG_PASSWORD"),
        )
        temp_conn.autocommit = True
        temp_cursor = temp_conn.cursor()

        # 2️⃣ Check if database exists
        temp_cursor.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s",
            (db_name,)
        )

        exists = temp_cursor.fetchone()

        # 3️⃣ Create database if not exists
        if not exists:
            temp_cursor.execute(f'CREATE DATABASE "{db_name}"')

        temp_cursor.close()
        temp_conn.close()

        # 4️⃣ Now connect to buyback
        self.conn = psycopg2.connect(
            host=os.getenv("PG_HOST", "localhost"),
            database=db_name,
            user=os.getenv("PG_USER", "postgres"),
            password=os.getenv("PG_PASSWORD"),
        )

        self.cursor = self.conn.cursor()

        # 5️⃣ Create tables
        self.create_tables()
        self.conn.commit()

    def close_spider(self, spider):
        self.conn.commit()
        self.cursor.close()
        self.conn.close()

    # =====================================================
    # TABLE CREATION
    # =====================================================

    def create_tables(self):
        self.cursor.execute("""CREATE EXTENSION IF NOT EXISTS "uuid-ossp";""")

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            image_url TEXT,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        );
        """)

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS brands (
            id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            logo_url TEXT,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        );
        """)

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS category_brands (
            category_id UUID REFERENCES categories(id) ON DELETE CASCADE,
            brand_id UUID REFERENCES brands(id) ON DELETE CASCADE,
            PRIMARY KEY (category_id, brand_id)
        );
        """)

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS attributes (
            id UUID PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            label TEXT,
            type TEXT CHECK (type IN ('select','radio','text')) NOT NULL
        );
        """)

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS attribute_options (
            id UUID PRIMARY KEY,
            attribute_id UUID REFERENCES attributes(id) ON DELETE CASCADE,
            value TEXT NOT NULL,
            sku_part TEXT,
            UNIQUE(attribute_id, value)
        );
        """)

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            image_url TEXT,
            max_price float,
            BuyWorld_Price float,
            brand_id UUID REFERENCES brands(id) ON DELETE CASCADE,
            category_id UUID REFERENCES categories(id) ON DELETE CASCADE,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        );
        """)

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_attributes (
            id UUID PRIMARY KEY,
            product_id UUID REFERENCES products(id) ON DELETE CASCADE,
            attribute_id UUID REFERENCES attributes(id) ON DELETE CASCADE,
            sort_order DECIMAL,
            UNIQUE(product_id, attribute_id)
        );
        """)

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_variants (
            id UUID PRIMARY KEY,
            product_id UUID REFERENCES products(id) ON DELETE CASCADE,
            price DECIMAL NOT NULL,
            sku TEXT,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        );
        """)

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS variant_values (
            id UUID PRIMARY KEY,
            variant_id UUID REFERENCES product_variants(id) ON DELETE CASCADE,
            attribute_option_id UUID REFERENCES attribute_options(id) ON DELETE CASCADE,
            UNIQUE(variant_id, attribute_option_id)
        );
        """)

    # =====================================================
    # MAIN PIPELINE LOGIC
    # =====================================================

    def process_item(self, item, spider):
        print('in the pipeline')

        category_id = self.get_or_create_category(item["Category"])
        brand_id = self.get_or_create_brand(item["Brand"])
        self.ensure_category_brand(category_id, brand_id)

        storage = item["Capacity"]
        # product_name = self.clean_product_name(item["Title"], storage)
        product_name = item.get('Name','')
        max = item.get("Maximum_Price",'')

        buyblack = item.get("Buybackworld_Price",'')

        product_id = self.get_or_create_product(
            product_name,
            brand_id,
            category_id,
            item.get("image_url"),
            max,
            buyblack
            # ✅ ADD THIS

        )

        # Ensure attributes exist
        storage_attr = self.get_or_create_attribute("Storage","radio")
        condition_attr = self.get_or_create_attribute("Condition","radio")
        variant_attr = self.get_or_create_attribute("Network","select")

        variant_value =item.get('Network','')


        # Assign attributes to product (required by schema)
        self.assign_attribute_to_product(product_id, storage_attr)
        self.assign_attribute_to_product(product_id, condition_attr)

        self.assign_attribute_to_product(product_id, variant_attr)


        # Create variants
        condition = item.get('Condition','')

        price = item.get("Maximum_Price")

        clean_price = float(
            price
        )

        variant_id = self.create_variant(product_id, clean_price)

        # Storage option
        storage_option = self.get_or_create_option(storage_attr, storage)

        # Condition option
        condition_option = self.get_or_create_option(condition_attr, condition)

        # Variant option (AT&T / Verizon etc)
        variant_option = self.get_or_create_option(variant_attr, variant_value)

        # Assign variant values
        self.assign_variant_option(variant_id, storage_option)
        self.assign_variant_option(variant_id, condition_option)

        self.assign_variant_option(variant_id, variant_option)

        self.conn.commit()
        return item

    # =====================================================
    # HELPERS
    # =====================================================

    def extract_storage(self, title):
        match = re.search(r'(\d+(?:GB|TB))', title, re.IGNORECASE)
        return match.group(1) if match else "Unknown"

    def clean_product_name(self, title, storage):
        return title.replace(storage, "").strip()

    # ---------------- Category ----------------

    def get_or_create_category(self, name):
        slug = slugify(name)
        self.cursor.execute("SELECT id FROM categories WHERE slug=%s", (slug,))
        result = self.cursor.fetchone()
        if result:
            return result[0]

        new_id = str(uuid.uuid4())
        self.cursor.execute("""
            INSERT INTO categories (id, name, slug, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s)
        """, (new_id, name, slug, datetime.utcnow(), datetime.utcnow()))
        return new_id

    # ---------------- Brand ----------------

    def get_or_create_brand(self, name):
        slug = slugify(name)
        self.cursor.execute("SELECT id FROM brands WHERE slug=%s", (slug,))
        result = self.cursor.fetchone()
        if result:
            return result[0]

        new_id = str(uuid.uuid4())
        self.cursor.execute("""
            INSERT INTO brands (id, name, slug, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s)
        """, (new_id, name, slug, datetime.utcnow(), datetime.utcnow()))
        return new_id

    def ensure_category_brand(self, category_id, brand_id):
        self.cursor.execute("""
            INSERT INTO category_brands (category_id, brand_id)
            VALUES (%s,%s)
            ON CONFLICT DO NOTHING
        """, (category_id, brand_id))

    # ---------------- Product ----------------

    def get_or_create_product(self, name, brand_id, category_id,image_url,price,buyblack):
        slug = slugify(name)
        self.cursor.execute("SELECT id FROM products WHERE slug=%s", (slug,))
        result = self.cursor.fetchone()
        if result:
            return result[0]

        new_id = str(uuid.uuid4())


        self.cursor.execute("""
            INSERT INTO products
            (id,name,slug,image_url,max_price,BuyWorld_Price,brand_id,category_id,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            new_id,
            name,
            slug,
            image_url,
            price,
            buyblack,
            brand_id,
            category_id,
            datetime.utcnow(),
            datetime.utcnow()
        ))

        return new_id


        # ---------------- Attributes ----------------
    def get_or_create_attribute(self, name,type):
        self.cursor.execute("SELECT id FROM attributes WHERE name=%s", (name,))
        result = self.cursor.fetchone()
        if result:
            return result[0]

        new_id = str(uuid.uuid4())
        self.cursor.execute("""
            INSERT INTO attributes (id,name,label,type)
            VALUES (%s,%s,%s,%s)
        """, (new_id, name, name,type))
        return new_id

    def get_or_create_option(self, attribute_id, value):
        self.cursor.execute("""
            SELECT id FROM attribute_options
            WHERE attribute_id=%s AND value=%s
        """, (attribute_id, value))
        result = self.cursor.fetchone()
        if result:
            return result[0]

        new_id = str(uuid.uuid4())
        self.cursor.execute("""
            INSERT INTO attribute_options (id,attribute_id,value)
            VALUES (%s,%s,%s)
        """, (new_id, attribute_id, value))
        return new_id

    def assign_attribute_to_product(self, product_id, attribute_id):
        self.cursor.execute("""
            INSERT INTO product_attributes (id,product_id,attribute_id,sort_order)
            VALUES (%s,%s,%s,0)
            ON CONFLICT (product_id,attribute_id) DO NOTHING
        """, (str(uuid.uuid4()), product_id, attribute_id))

    # ---------------- Variants ----------------

    def create_variant(self, product_id, price):
        new_id = str(uuid.uuid4())
        self.cursor.execute("""
            INSERT INTO product_variants
            (id,product_id,price,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s)
        """, (new_id, product_id, price,
              datetime.utcnow(), datetime.utcnow()))
        return new_id

    def assign_variant_option(self, variant_id, option_id):
        self.cursor.execute("""
            INSERT INTO variant_values
            (id,variant_id,attribute_option_id)
            VALUES (%s,%s,%s)
            ON CONFLICT DO NOTHING
        """, (str(uuid.uuid4()), variant_id, option_id))

