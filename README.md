# XPilot — AI-Native CRM

XPilot is an AI-native Customer Relationship Management (CRM) platform that combines customer intelligence, campaign automation, attribution analytics, and Gemini-powered marketing strategy generation into a single product.

Built for data-driven marketing teams, XPilot helps users understand customers, identify opportunities, create targeted campaigns, simulate multi-channel engagement, and generate actionable insights using AI.

---

## Key Highlights

* AI-powered campaign strategist
* Customer segmentation and intelligence
* Campaign delivery simulation
* Attribution tracking and revenue analytics
* Customer 360 profiles
* Natural language customer search
* Live campaign performance monitoring
* Gemini-powered recommendations
* Responsive design (Desktop, Tablet, Mobile)
* One-command local setup

---

## Tech Stack

| Layer     | Technology                           |
| --------- | ------------------------------------ |
| Backend   | Flask 3.x                            |
| Database  | SQLite + SQLAlchemy                  |
| AI        | Gemini 2.5 Flash                     |
| Frontend  | HTMX + Alpine.js                     |
| Styling   | Tailwind CSS                         |
| Messaging | Simulated WhatsApp, Email, SMS, Push |
| Analytics | SQLAlchemy Aggregations              |
| Callbacks | Receipt Webhook API                  |

---

## Core Features

### Customer Segmentation

Customers are automatically classified into:

* Loyal
* At Risk
* Dormant
* New

Segmentation is calculated using customer purchase behavior and engagement history.

---

### AI Strategist

The AI Strategist transforms marketing goals into campaign plans.

Example:

> Reward loyal customers with an exclusive offer

XPilot automatically:

1. Analyzes customer data
2. Selects target audience
3. Chooses the best channel
4. Generates message variants
5. Forecasts campaign impact
6. Produces a complete campaign strategy

---

### Customer Intelligence

Natural language CRM queries such as:

* Who is likely to churn?
* Show dormant customers
* Find loyal customers
* Customers with spend above ₹20,000
* Show Bangalore customers

allow users to explore customer data conversationally.

---

### Customer 360

Each customer profile includes:

* Personal details
* Purchase history
* Customer timeline
* Segment classification
* AI-generated summary
* Revenue contribution

---

### Campaign Engine

Supports:

* Campaign creation
* Audience targeting
* Delivery simulation
* Open tracking
* Click tracking
* Conversion tracking
* Revenue attribution

---

### Attribution Tracking

XPilot tracks campaign effectiveness by connecting customer engagement with simulated purchase behavior.

Metrics include:

* Conversions
* Conversion Rate
* Attributed Revenue
* Revenue Impact

---

### AI Recommendations

Gemini continuously analyzes CRM and campaign data to generate:

* Revenue opportunities
* Retention recommendations
* Segment insights
* Campaign optimization suggestions

---

## AI Workflow

The AI Strategist uses a structured tool chain:

```text
generate_final_strategy(goal)
    │
    ├── analyze_customer_base()
    ├── select_target_segment()
    ├── choose_best_channel()
    ├── generate_message_variants()
    ├── forecast_revenue()
    └── generate_final_strategy()
```

This approach separates business logic from AI generation and makes recommendations more explainable.

---

## Project Structure

```text
.
├── app.py
├── channel_service.py
├── config.py
├── models.py
├── seed.py
├── requirements.txt
├── deployment.md
├── README.md
├── .env.example
│
├── instance/
│   └── xenopilot.db
│
├── routes/
│   ├── auth.py
│   ├── dashboard.py
│   ├── campaigns.py
│   ├── customers.py
│   ├── ai_routes.py
│   ├── pages.py
│   └── receipts.py
│
└── templates/
    ├── base.html
    ├── login.html
    ├── dashboard.html
    ├── strategist.html
    ├── campaigns.html
    ├── customers.html
    ├── customer_360.html
    ├── insights.html
    ├── architecture.html
    └── htmx/
```

---

## Local Setup

### 1. Clone Repository

```bash
git clone <repository-url>
cd XPilot
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables

Create a `.env` file:

```env
GEMINI_API_KEY=your_gemini_api_key
SESSION_SECRET=your_secret_key
PORT=8080
```

### 4. Run Application

```bash
python app.py
```

The database is automatically created and seeded with demo data on first startup.

---

## Demo Credentials

| Field    | Value                                   |
| -------- | --------------------------------------- |
| Email    | [demo@xpilot.ai](mailto:demo@xpilot.ai) |
| Password | demo1234                                |

---

## Deployment

Example production command:

```bash
gunicorn -w 4 -b 0.0.0.0:8080 app:app
```

Environment variables should be configured through the hosting provider.

---

## Engineering Decisions

### Why Flask?

* Lightweight
* Easy to understand
* Minimal boilerplate
* Ideal for rapid product development

### Why SQLite?

* Zero configuration
* Simple deployment
* Suitable for assignment-scale workloads

### Why HTMX?

* No frontend build pipeline
* Server-driven UI
* Faster iteration

### Why Gemini?

* Fast inference
* Strong structured output
* Cost-effective for prototyping
* Easy Python integration

---

## Tradeoffs

This project was intentionally optimized for assignment scale and demonstration purposes.

Current implementation:

* SQLite instead of PostgreSQL
* Background threads instead of Celery
* Polling instead of WebSockets
* Simulated messaging providers instead of real integrations

These decisions reduced deployment complexity while preserving core product functionality.

---

