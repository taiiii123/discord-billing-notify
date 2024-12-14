import os
import boto3
import requests
from datetime import datetime, timedelta, date


def get_total_billing(client) -> dict:
    (start_date, end_date) = get_total_cost_date_range()

    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ce.html#CostExplorer.Client.get_cost_and_usage
    response = client.get_cost_and_usage(
        TimePeriod={
            'Start': start_date,
            'End': end_date
        },
        Granularity='MONTHLY',
        Metrics=[
            'AmortizedCost'
        ]
    )
    return {
        'start': response['ResultsByTime'][0]['TimePeriod']['Start'],
        'end': response['ResultsByTime'][0]['TimePeriod']['End'],
        'billing': response['ResultsByTime'][0]['Total']['AmortizedCost']['Amount'],
    }

def get_service_billings(client) -> list:
    (start_date, end_date) = get_total_cost_date_range()

    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ce.html#CostExplorer.Client.get_cost_and_usage
    response = client.get_cost_and_usage(
        TimePeriod={
            'Start': start_date,
            'End': end_date
        },
        Granularity='MONTHLY',
        Metrics=[
            'AmortizedCost'
        ],
        GroupBy=[
            {
                'Type': 'DIMENSION',
                'Key': 'SERVICE'
            }
        ]
    )

    billings = []

    for item in response['ResultsByTime'][0]['Groups']:
        billings.append({
            'service_name': item['Keys'][0],
            'billing': item['Metrics']['AmortizedCost']['Amount']
        })
    return billings

def get_message(total_billing: dict, service_billings: list) -> (str, str):
    start = datetime.strptime(total_billing['start'], '%Y-%m-%d').strftime('%m/%d')

    # Endの日付は結果に含まないため、表示上は前日にしておく
    end_today = datetime.strptime(total_billing['end'], '%Y-%m-%d')
    end_yesterday = (end_today - timedelta(days=1)).strftime('%m/%d')

    total = round(float(total_billing['billing']), 2)

    title = f'{start}～{end_yesterday}の請求額は、{total:.2f} USDです。'

    details = []
    for item in service_billings:
        service_name = item['service_name']
        billing = round(float(item['billing']), 2)

        if billing == 0.0:
            # 請求無し（0.0 USD）の場合は、内訳を表示しない
            continue
        details.append(f'　・{service_name}: {billing:.2f} USD')

    return title, '\n'.join(details)

def get_total_cost_date_range() -> (str, str):
    start_date = get_begin_of_month()
    end_date = get_today()

    # get_cost_and_usage()のstartとendに同じ日付は指定不可のため、
    # 「今日が1日」なら、「先月1日から今月1日（今日）」までの範囲にする
    if start_date == end_date:
        end_of_month = datetime.strptime(start_date, '%Y-%m-%d') + timedelta(days=-1)
        begin_of_month = end_of_month.replace(day=1)
        return begin_of_month.date().isoformat(), end_date
    return start_date, end_date

def get_begin_of_month() -> str:
    return date.today().replace(day=1).isoformat()

def get_prev_day(prev: int) -> str:
    return (date.today() - timedelta(days=prev)).isoformat()

def get_today() -> str:
    return date.today().isoformat()

def get_message(total_billing: dict, service_billings: list) -> (str, str):
    start = datetime.strptime(total_billing['start'], '%Y-%m-%d').strftime('%m/%d')

    # Endの日付は結果に含まないため、表示上は前日にしておく
    end_today = datetime.strptime(total_billing['end'], '%Y-%m-%d')
    end_yesterday = (end_today - timedelta(days=1)).strftime('%m/%d')

    title = f'{start}～{end_yesterday}の請求額'

    details = []
    for item in service_billings:
        service_name = item['service_name']
        billing = round(float(item['billing']), 2)

        if billing == 0.0:
            # 請求無し（0.0 USD）の場合は、内訳を表示しない
            continue
        details.append(f'・{service_name}： {billing:.2f} USD')

    return title, '\n'.join(details)

def determine_alert(cost: float, budget: float) -> (str, str):
    # 無料のとき
    if cost == 0:
        return 0x1abc9c, '支払いはありません'
    # 予算内のとき
    elif cost < budget:
        return 0x3498db, '支払いは予算内です'
    # 予算を超えたとき
    else:
        return 0xe74c3c, '支払いは予算を超えています！！'

def lambda_handler(event, context):

    # 変数
    ACCOUNTID = os.environ['accountId']
    WEBHOOK_URL = os.environ['WebhookURL']
    BUDGET_NAME = os.environ['budgetName']
    REGION_NAME = os.environ['regionName']
    ICON = os.environ['awsIcon']

    # cost explorer client
    cs_client = boto3.client('ce', region_name=REGION_NAME)
    # budgets client
    budgets_client = boto3.client('budgets', region_name=REGION_NAME)

    # 合計とサービス毎の請求額を取得する
    total_billing = get_total_billing(cs_client)
    service_billings = get_service_billings(cs_client)

    (title, detail) = get_message(total_billing, service_billings)
    cost = float(total_billing['billing'])

    # AWSから料金や予算を取得
    responce = budgets_client.describe_budget(
            AccountId=ACCOUNTID,
            BudgetName=BUDGET_NAME
    )

    # 代入
    budget = float(responce['Budget']['BudgetLimit']['Amount']) # 予算

    # webhook用データ
    username = 'AWSコスト通知'
    color, alert_msg = determine_alert(cost, budget)

    # ライン
    line = '\n' + '-' * 50

    # Webhook
    data = {
        'username': username,
        'avatar_url': ICON,
        'embeds': [
            {
                'title': title,
                'description': f'総請求額： {cost:.2f} USD',
                'color': color,
                'fields': [
                    {
                        'name': '[予算]',
                        'value': f'{budget:.2f} USD'
                                + f'　　{alert_msg}'
                                + line,
                        'inline': False
                    },
                    {
                        'name': '[各サービスの利用料金]',
                        'value': detail,
                        'inline': False
                    },
                ]
            }
        ]
    }

    # POST
    try:
        requests.post(WEBHOOK_URL, json=data)
    except requests.exceptions.RequestException as e:
        print(f'Webhook送信エラー: {e}')


if __name__ == '__main__':
    lambda_handler(None, None)