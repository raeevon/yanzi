import os
import asyncio
from typing import List, Optional, Dict
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright

SERVICE_TOKEN = os.environ.get("SERVICE_TOKEN", "change-me")

app = FastAPI(title="Yanzi Playwright Microservice", version="1.0.0")

class Item(BaseModel):
    # Например: {"name": "Сяо-лон-бао", "quantity": 2}
    name: str = Field(..., description="Отображаемое на витрине название")
    quantity: int = Field(1, ge=1, description="Штук к добавлению")

class Customer(BaseModel):
    name: str
    phone: str
    address: Optional[str] = None
    comment: Optional[str] = None
    email: Optional[str] = None

class OrderPayload(BaseModel):
    items: List[Item]
    customer: Customer
    # если у сайта есть выбор ресторана/доставки/самовывоза — заполняйте при необходимости
    delivery_type: Optional[str] = Field(None, description="delivery|pickup")
    extra: Optional[Dict] = None

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/order")
async def make_order(payload: OrderPayload, x_service_token: Optional[str] = Header(None)):
    if x_service_token != SERVICE_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            locale="ru-RU",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        )
        page = await context.new_page()
        try:
            # 1) Витрина
            await page.goto("https://yanzi.ru/products", wait_until="networkidle", timeout=90000)

            # Иногда всплывают GDPR/куки-соглашения — пытаемся закрыть
            for selector in [
                "button:has-text('Принять')",
                "button:has-text('Согласен')",
                "[data-testid='cookie-accept']",
            ]:
                if await page.locator(selector).first.is_visible(timeout=1000).catch(lambda _: False):
                    await page.locator(selector).first.click().catch(lambda _: None)

            # 2) Добавляем позиции
            added = []
            for it in payload.items:
                # Поиск карточки по тексту названия
                card = page.get_by_text(it.name, exact=False).locator("xpath=ancestor::*[self::article or self::div][1]")
                # Кнопка "В корзину" внутри карточки
                add_btn = card.get_by_role("button", name=lambda n: "в корзину" in n.lower() or "добавить" in n.lower())
                for _ in range(it.quantity):
                    await add_btn.first.click()
                    # Небольшой задержки достаточно, чтобы инкремент отработал
                    await page.wait_for_timeout(400)
                added.append({"name": it.name, "quantity": it.quantity})

            # 3) Переход в корзину
            # Пытаемся нажать на общую кнопку корзины в шапке
            cart_button = page.get_by_role("button", name=lambda n: "корзин" in n.lower() or "basket" in n.lower())
            if await cart_button.count() == 0:
                cart_button = page.locator("[href*='cart'], [data-testid='open-cart'], a:has-text('Корзина')")
            await cart_button.first.click()
            await page.wait_for_load_state("networkidle")

            # 4) Оформление заказа (упрощённая схема)
            # Имя
            for selector in ["input[name='name']", "input[placeholder*='Имя']"]:
                loc = page.locator(selector)
                if await loc.count():
                    await loc.fill(payload.customer.name)
                    break

            # Телефон
            for selector in ["input[type='tel']", "input[name='phone']", "input[placeholder*='Телефон']"]:
                loc = page.locator(selector)
                if await loc.count():
                    await loc.fill(payload.customer.phone)
                    break

            # Адрес
            if payload.customer.address:
                for selector in ["input[name='address']", "textarea[name='address']", "input[placeholder*='Адрес']"]:
                    loc = page.locator(selector)
                    if await loc.count():
                        await loc.fill(payload.customer.address)
                        break

            # Комментарий
            if payload.customer.comment:
                for selector in ["textarea[name='comment']", "textarea[placeholder*='Комментар']"]:
                    loc = page.locator(selector)
                    if await loc.count():
                        await loc.fill(payload.customer.comment)
                        break

            # Email (если есть)
            if payload.customer.email:
                for selector in ["input[type='email']", "input[name='email']"]:
                    loc = page.locator(selector)
                    if await loc.count():
                        await loc.fill(payload.customer.email)
                        break

            # Тип доставки/самовывоз (если на сайте есть переключатель)
            if payload.delivery_type:
                if payload.delivery_type == "pickup":
                    maybe = page.get_by_label("Самовывоз").or_(page.get_by_text("Самовывоз"))
                    if await maybe.count():
                        await maybe.first.click()
                elif payload.delivery_type == "delivery":
                    maybe = page.get_by_label("Доставка").or_(page.get_by_text("Доставка"))
                    if await maybe.count():
                        await maybe.first.click()

            # Подтвердить/Отправить
            submit = page.get_by_role("button", name=lambda n: any(w in n.lower() for w in ["оформ", "заказ", "подтвер", "далее", "оплат"]))
            if await submit.count() == 0:
                submit = page.locator("button[type='submit']")
            await submit.first.click()

            # Ждём подтверждения
            await page.wait_for_load_state("networkidle")
            # Пробуем вытащить номер заказа/уведомление
            confirmation_text = ""
            for sel in [
                "text=/заказ.*(принят|оформлен|успешно)/i",
                "[data-testid='order-success']",
                "h1, h2, .success, .order-number"
            ]:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    confirmation_text = (await loc.inner_text()).strip()
                    break

            # Скриншот на память (Railway — эпфемерная ФС; годится для лога запроса)
            await page.screenshot(path="/tmp/checkout_result.png", full_page=True)

            return {
                "status": "ok",
                "added": added,
                "confirmation": confirmation_text or "Подтверждение не найдено, проверьте заказ в админке.",
                "screenshot": "sandbox:/tmp/checkout_result.png"
            }
        finally:
            await context.close()
            await browser.close()
