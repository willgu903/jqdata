import inspect
import logging
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from typing import Optional

import pandas as pd
import requests

from .error import AuthError, TimeOutError, ServerError, UnknownError

logger = logging.getLogger(__name__)


def _validate_date(date: Optional[str]):
    if date:
        try:
            datetime.strptime('2019-01-01', '%Y-%m-%d')
        except ValueError:
            raise ValueError(f"incorrect date format for {date}")


@dataclass
class JqClient:
    mob: str
    pwd: str

    url = "https://dataapi.joinquant.com/apis"
    token = None

    def _post(self, payload, include_caller=True, include_token=True, res_format='string'):
        """
        1. 504意为请求超时，可以稍后再试。如果出现在含有count参数的接口，可以减小count值重新请求。
        2. 500意为服务器报错，可以先检查参数是否正确。也可能是服务器负载过高，稍后再试。
        3. 目前限制规则为每个账号每分钟1800次请求。后续看情况可逐步放宽限制。
        :param payload:
        :param include_caller:
        :param include_token:
        :param res_format:
        :return:
        """
        assert res_format in ('string', 'list', 'csv', 'json')
        if 'self' in payload:
            payload.pop('self')
        if include_caller:
            payload['method'] = inspect.stack()[1][3]
        payload = {k: v for k, v in payload.items() if v is not None}
        for k, v in payload.items():
            if k.lower().endswith('date'):
                _validate_date(v)
        if include_token:
            if not self.token:
                raise ValueError("token is empty, please initialize first")
            else:
                payload['token'] = self.token
        res = requests.post(self.url, json=payload)
        if res.status_code == 504:
            raise TimeOutError("server timed out. please try to reduce [count] or try later")
        elif res.status_code == 500:
            raise ServerError("server error. please check your parameter or try later")
        elif res.ok:
            if res.text.startswith('error:auth failed'):
                raise AuthError("auth failed, please check your credentials")
            elif res.text.startswith("error:"):
                raise UnknownError(f"error return with ok status. message: ${res.text}")
            elif res_format == 'list':
                return res.text.split('\n')
            elif res_format == 'csv':
                return pd.read_csv(StringIO(res.text))
            elif res_format == 'json':
                return res.json()
            else:
                return res.text
        else:
            raise UnknownError(f"post returns unsuccessful with status {res.status_code}. message: {res.text}")

    def initialize(self):
        """
        try to get current token first. if not available, try to generate a new token
        当天获取的token有效期截止到当天夜里23:59:59。每次获取token后，之前的token都会失效
        :return:
        """
        payload = {
            'method': 'get_current_token',
            'mob': self.mob,
            'pwd': self.pwd
        }
        try:
            res = self._post(payload, include_caller=False, include_token=False, res_format='string')
            logger.info("use existing token")
        except UnknownError:
            payload['method'] = 'get_token'
            res = self._post(payload, include_caller=False, include_token=False, res_format='string')
            logger.info("generated a new token")
        self.token = res

    def get_all_securities(self, code: str, date: Optional[str]):
        """
        获取平台支持的所有股票、基金、指数、期货信息
        :param code: 证券类型
        :param date: date: 日期，用于获取某日期还在上市的证券信息，date为空时表示获取所有日期的标的信息
        :return:
            code: 标的代码
            display_name: 中文名称
            name: 缩写简称
            start_date: 上市日期
            end_date: 退市日期，如果没有退市则为2200-01-01
            type: 类型: stock(股票)，index(指数)，etf(ETF基金)，fja（分级A），fjb（分级B），fjm（分级母基金），
                mmf（场内交易的货币基金）open_fund（开放式基金）, bond_fund（债券基金）, stock_fund（股票型基金）,
                QDII_fund（QDII 基金）, money_market_fund（场外交易的货币基金）, mixture_fund（混合型基金）, options（期权）
        """
        assert code in (
            'stock',
            'fund',
            'index',
            'futures',
            'etf',
            'lof',
            'fja',
            'fjb',
            'QDII_fund',
            'open_fund',
            'bond_fund',
            'stock_fund',
            'money_market_fund',
            'mixture_fund',
            'options'
        )
        return self._post(locals(), res_format='csv')

    def get_security_info(self, code: str):
        """
        获取股票/基金/指数的信息
        :param code: 证券代码
        :return:
            code: 标的代码
            display_name: 中文名称
            name: 缩写简称
            start_date: 上市日期, [datetime.date] 类型
            end_date: 退市日期， [datetime.date] 类型, 如果没有退市则为2200-01-01
            type: 类型，stock(股票)，index(指数)，etf(ETF基金)，fja（分级A），fjb（分级B）
            parent: 分级基金的母基金代码
        """
        return self._post(locals(), res_format='csv')

    def get_index_stocks(self, code: str, date: str):
        """
        获取一个指数给定日期在平台可交易的成分股列表
        :param code: 指数代码
        :param date: 查询日期
        :return: 股票代码
        """
        return self._post(locals(), res_format='list')

    def get_margincash_stocks(self, date: Optional[str]):
        """
        获取融资标的列表
        :param date: 查询日期，默认为前一交易日
        :return: 返回指定日期上交所、深交所披露的的可融资标的列表
        """
        _validate_date(date)
        return self._post(locals(), res_format='list')

    def get_marginsec_stocks(self, date: Optional[str]):
        """
        获取融券标的列表
        :param date: 查询日期，默认为前一交易日
        :return: 返回指定日期上交所、深交所披露的的可融券标的列表
        """
        return self._post(locals(), res_format='list')

    def get_locked_shares(self, code: str, date: str, end_date: str):
        """
        获取指定日期区间内的限售解禁数据
        :param code: 股票代码
        :param date: 开始日期
        :param end_date: 结束日期
        :return:
            day: 解禁日期
            code: 股票代码
            num: 解禁股数
            rate1: 解禁股数/总股本
            rate2: 解禁股数/总流通股本
        """
        return self._post(locals(), res_format='csv')

    def get_index_weights(self, code: str, date: str):
        """
        获取指数成份股给定日期的权重数据，每月更新一次
        :param code: 代表指数的标准形式代码， 形式：指数代码.交易所代码，例如"000001.XSHG"
        :param date: 查询权重信息的日期，形式："%Y-%m-%d"，例如"2018-05-03"
        :return:
            code: 指数代码
            display_name: 股票名称
            date: 日期
            weight: 权重
        """
        _validate_date(date)
        return self._post(locals(), res_format='csv')

    def get_industries(self, code: str):
        """
        按照行业分类获取行业列表
        :param code: 行业代码
        :return:
            index: 行业代码
            name: 行业名称
            start_date: 开始日期
        """
        assert code in ('sw_l1', 'jq_l1', 'jq_l2', 'zjw')
        return self._post(locals(), res_format='csv')

    def get_industry(self, code: str, date: str):
        """
        查询股票所属行业
        :param code: 证券代码
        :param date: 查询的日期
        :return:
            industry：一级行业代码
            industry_code：二级行业代码
            industry_name：行业名称
        """
        return self._post(locals(), res_format='csv')

    def get_industry_stocks(self, code: str, date: str):
        """
        获取在给定日期一个行业的所有股票
        :param code: 行业编码
        :param date: 查询日期
        :return: 返回股票代码的list
        """
        return self._post(locals(), res_format='list')

    def get_concepts(self):
        """
        获取概念板块列表
        :return:
            code: 概念代码
            name: 概念名称
            start_date: 开始日期
        """
        return self._post(dict(), res_format='csv')

    def get_concept_stocks(self, code: str, date: str):
        """
        获取在给定日期一个概念板块的所有股票
        :param code: 概念板块编码
        :param date: 查询日期
        :return: 股票代码
        """
        return self._post(locals(), res_format='list')

    def get_trade_days(self, date: str, end_date: str):
        """
        获取指定日期范围内的所有交易日
        :param date: 开始日期
        :param end_date: 结束日期
        :return: 交易日日期
        """
        return self._post(locals(), res_format='list')

    def get_all_trade_days(self):
        """
        获取所有交易日
        :return: 交易日list
        """
        return self._post(dict(), res_format='list')

    def get_mtss(self, code: str, date: str, end_date: str):
        """
        获取一只股票在一个时间段内的融资融券信息
        :param code: 股票代码
        :param date: 开始日期
        :param end_date: 结束日期
        :return:
            date: 日期
            sec_code: 股票代码
            fin_value: 融资余额(元）
            fin_buy_value: 融资买入额（元）
            fin_refund_value: 融资偿还额（元）
            sec_value: 融券余量（股）
            sec_sell_value: 融券卖出量（股）
            sec_refund_value: 融券偿还量（股）
            fin_sec_value: 融资融券余额（元）
        """
        return self._post(locals(), res_format='csv')

    def get_money_flow(self, code: str, date: str, end_date: str):
        """
        获取一只股票在一个时间段内的资金流向数据，仅包含股票数据，不可用于获取期货数据
        :param code: 股票代码
        :param date: 开始日期
        :param end_date: 结束日期
        :return:
            date: 日期
            sec_code: 股票代码
            change_pct: 涨跌幅(%)
            net_amount_main: 主力净额(万): 主力净额 = 超大单净额 + 大单净额
            net_pct_main: 主力净占比(%): 主力净占比 = 主力净额 / 成交额
            net_amount_xl: 超大单净额(万): 超大单：大于等于50万股或者100万元的成交单
            net_pct_xl: 超大单净占比(%): 超大单净占比 = 超大单净额 / 成交额
            net_amount_l: 大单净额(万): 大单：大于等于10万股或者20万元且小于50万股或者100万元的成交单
            net_pct_l: 大单净占比(%): 大单净占比 = 大单净额 / 成交额
            net_amount_m: 中单净额(万): 中单：大于等于2万股或者4万元且小于10万股或者20万元的成交单
            net_pct_m: 中单净占比(%): 中单净占比 = 中单净额 / 成交额
            net_amount_s: 小单净额(万): 小单：小于2万股或者4万元的成交单
            net_pct_s: 小单净占比(%): 小单净占比 = 小单净额 / 成交额
        """
        return self._post(locals(), res_format='csv')

    def get_billboard_list(self, code: str, date: str, end_date: str):
        """
        获取指定日期区间内的龙虎榜数据
        :param code: 股票代码
        :param date: 开始日期
        :param end_date: 结束日期
        :return:
            code: 股票代码
            day: 日期
            direction: ALL 表示『汇总』，SELL 表示『卖』，BUY 表示『买』
            abnormal_code: 异常波动类型
            abnormal_name: 异常波动名称
            sales_depart_name: 营业部名称
            rank: 0 表示汇总， 1~5 表示买一到买五， 6~10 表示卖一到卖五
            buy_value: 买入金额
            buy_rate: 买入金额占比(买入金额/市场总成交额)
            sell_value: 卖出金额
            sell_rate: 卖出金额占比(卖出金额/市场总成交额)
            net_value: 净额(买入金额 - 卖出金额)
            amount: 市场总成交额
        """
        return self._post(locals(), res_format='csv')

    def get_future_contracts(self, code: str, date: str):
        """
        获取某期货品种在指定日期下的可交易合约标的列表
        :param code: 期货合约品种，如 AG (白银)
        :param date: 指定日期
        :return: 某一期货品种在指定日期下的可交易合约标的列表
        """
        return self._post(locals(), res_format='list')

    def get_dominant_future(self, code: str, date: str):
        """
        获取主力合约对应的标的
        :param code: 期货合约品种，如 AG (白银)
        :param date: 指定日期参数，获取历史上该日期的主力期货合约
        :return: 主力合约对应的期货合约
        """
        return self._post(locals(), res_format='list')

    def get_fund_info(self, code: str, date: str):
        """
        获取单个基金的基本信息
        :param code: 基金代码
        :param date: 查询日期， 默认日期是今天
        :return:
            fund_name: 基金全称
            fund_type: 基金类型
            fund_establishment_day: 基金成立日
            fund_manager: 基金管理人及基本信息
            fund_management_fee: 基金管理费
            fund_custodian_fee: 基金托管费
            fund_status: 基金申购赎回状态
            fund_size: 基金规模（季度）
            fund_share: 基金份额（季度）
            fund_asset_allocation_proportion: 基金资产配置比例（季度）
            heavy_hold_stocks: 基金重仓股（季度）
            heavy_hold_stocks_proportion: 基金重仓股占基金资产净值比例（季度）
            heavy_hold_bond: 基金重仓债券（季度）
            heavy_hold_bond_proportion: 基金重仓债券占基金资产净值比例（季度）
        """
        return self._post(locals(), res_format='json')

    def get_current_tick(self, code: str):
        """
        获取最新的 tick 数据
        :param code: 标的代码， 支持股票、指数、基金、期货等。 不可以使用主力合约和指数合约代码
        :return:
            time: 时间
            current: 当前价
            high: 截至到当前时刻的日内最高价
            low: 截至到当前时刻的日内最低价
            volume: 累计成交量
            money: 累计成交额
            position: 持仓量，期货使用
            a1_v~a5_v: 五档卖量
            a1_p~a5_p: 五档卖价
            b1_v~b5_v: 五档买量
            b1_p~b5_p: 五档买价
        """
        return self._post(locals(), res_format='csv')

    def get_current_ticks(self, code: str):
        """
        获取多标的最新的 tick 数据
        :param code: 标的代码， 多个标的使用,分隔。每次请求的标的必须是相同类型。标的类型包括： 股票、指数、场内基金、期货、期权
        :return:
            code: 标的代码
            time: 时间
            current: 当前价
            high: 截至到当前时刻的日内最高价
            low: 截至到当前时刻的日内最低价
            volume: 累计成交量
            money: 累计成交额
            position: 持仓量，期货使用
            a1_v~a5_v: 五档卖量
            a1_p~a5_p: 五档卖价
            b1_v~b5_v: 五档买量
            b1_p~b5_p: 五档买价
        """
        return self._post(locals(), res_format='csv')

    def get_extras(self, code: str, date: str, end_date: str):
        """
        获取基金净值/期货结算价等
        :param code: 证券代码
        :param date: 开始日期
        :param end_date: 结束日期
        :return:
            date: 日期
            is_st: 是否是ST，是则返回 1，否则返回 0。股票使用
            acc_net_value: 基金累计净值。基金使用
            unit_net_value: 基金单位净值。基金使用
            futures_sett_price: 期货结算价。期货使用
            futures_positions: 期货持仓量。期货使用
            adj_net_value: 场外基金的复权净值。场外基金使用
        """
        return self._post(locals(), res_format='csv')

    def get_price(self, code: str, count: int, unit: str, end_date: Optional[str], fq_ref_date: Optional[str]):
        """
        获取各种时间周期的bar数据，bar的分割方式与主流股票软件相同， 同时还支持返回当前时刻所在 bar 的数据
        :param code: 证券代码
        :param count: 大于0的整数，表示获取bar的条数，不能超过5000
        :param unit: 时间单位, 支持如下周期：1m, 5m, 15m, 30m, 60m, 120m, 1d, 1w, 1M。其中m表示分钟，d表示天，w表示周，M表示月
        :param end_date: 查询的截止时间，默认是今天
        :param fq_ref_date: 复权基准日期，该参数为空时返回不复权数据
        :return:
            date: 日期
            open: 开盘价
            close: 收盘价
            high: 最高价
            low: 最低价
            volume: 成交量
            money: 成交额
            当unit为1d时，包含以下返回值:
            paused: 是否停牌，0 正常；1 停牌
            high_limit: 涨停价
            low_limit: 跌停价
            avg: 当天均价
            pre_close：前收价
            当code为期货和期权时，包含以下返回值:
            open_interest 持仓量
        """
        assert 0 < count <= 5000
        assert unit in ('1m', '5m', '15m', '30m', '60m', '120m', '1d', '1w', '1M')
        return self._post(locals(), res_format='csv')

    def get_price_period(self, code: str, unit: str, date: str, end_date: str, fq_ref_date: Optional[str]):
        """
        指定开始时间date和结束时间end_date时间段，获取行情数据
        注： 当unit是1w或1M时，第一条数据是开始时间date所在的周或月的行情。
             当unit为分钟时，第一条数据是开始时间date所在的一个unit切片的行情。最大获取1000个交易日数据
        :param code: 证券代码
        :param unit: 时间单位, 支持如下周期：1m, 5m, 15m, 30m, 60m, 120m, 1d, 1w, 1M。其中m表示分钟，d表示天，w表示周，M表示月
        :param date: 开始时间，不能为空，格式2018-07-03或2018-07-03 10:40:00，如果是2018-07-03则默认为2018-07-03 00:00:00
        :param end_date: 结束时间，不能为空，格式2018-07-03或2018-07-03 10:40:00，如果是2018-07-03则默认为2018-07-03 23:59:00
        :param fq_ref_date: 复权基准日期，该参数为空时返回不复权数据
        :return:
            date: 日期
            open: 开盘价
            close: 收盘价
            high: 最高价
            low: 最低价
            volume: 成交量
            money: 成交额
            当unit为1d时，包含以下返回值:
            paused: 是否停牌，0 正常；1 停牌
            high_limit: 涨停价
            low_limit: 跌停价
            当code为期货和期权时，包含以下返回值:
            open_interest 持仓量
        """
        assert unit in ('1m', '5m', '15m', '30m', '60m', '120m', '1d', '1w', '1M')
        return self._post(locals(), res_format='csv')

    def get_ticks(self, code: str, count: Optional[int], end_date: str, skip: bool = True):
        """
        获取tick数据
        股票部分， 支持 2010-01-01 至今的tick数据，提供买五卖五数据
        期货部分， 支持 2010-01-01 至今的tick数据，提供买一卖一数据。
            如果要获取主力合约的tick数据，可以先使用get_dominant_future获取主力合约对应的标的
        期权部分，支持 2017-01-01 至今的tick数据，提供买五卖五数据
        :param code: 证券代码
        :param count: 取出指定时间区间内前多少条的tick数据，如不填count，则返回end_date一天内的全部tick
        :param end_date: 结束日期，格式2018-07-03或2018-07-03 10:40:00
        :param skip: 默认为true，过滤掉无成交变化的tick数据；
            当skip=false时，返回的tick数据会保留从2019年6月25日以来无成交有盘口变化的tick数据。
            由于期权成交频率低，所以建议请求期权数据时skip设为false
        :return:
            time: 时间
            current: 当前价
            high: 当日最高价
            low: 当日最低价
            volume: 累计成交量（股）
            money: 累计成交额
            position: 持仓量，期货使用
            a1_v~a5_v: 五档卖量
            a1_p~a5_p: 五档卖价
            b1_v~b5_v: 五档买量
            b1_p~b5_p: 五档买价
        """
        return self._post(locals(), res_format='csv')

    def get_ticks_period(self, code: str, date: str, end_date: str, skip: bool = True):
        """
        按时间段获取tick数据
        股票部分， 支持 2010-01-01 至今的tick数据，提供买五卖五数据
        期货部分， 支持 2010-01-01 至今的tick数据，提供买一卖一数据。
            如果要获取主力合约的tick数据，可以先使用get_dominant_future获取主力合约对应的标的
        期权部分，支持 2017-01-01 至今的tick数据，提供买五卖五数据
        注：如果时间跨度太大、数据量太多则可能导致请求超时，所有请控制好data-end_date之间的间隔！
        :param code: 证券代码
        :param date: 开始时间，格式2018-07-03或2018-07-03 10:40:00
        :param end_date: 结束时间，格式2018-07-03或2018-07-03 10:40:00
        :param skip: 默认为true，过滤掉无成交变化的tick数据；
            当skip=false时，返回的tick数据会保留从2019年6月25日以来无成交有盘口变化的tick数据。
        :return:
            time: 时间
            current: 当前价
            high: 当日最高价
            low: 当日最低价
            volume: 累计成交量（手）
            money: 累计成交额
            position: 持仓量，期货使用
            a1_v~a5_v: 五档卖量
            a1_p~a5_p: 五档卖价
            b1_v~b5_v: 五档买量
            b1_p~b5_p: 五档买价
        """
        return self._post(locals(), res_format='csv')

    def get_factor_values(self, code: str, columns: str, date: str, end_date: str):
        """
        聚宽因子库数据, ref: https://www.joinquant.com/help/api/help?name=factor_values
        注：
            为保证数据的连续性，所有数据基于后复权计算
            为了防止单次返回数据时间过长，尽量较少查询的因子数和时间段
            如果第一次请求超时，尝试重试
        :param code: 单只股票代码
        :param columns: 因子名称，因子名称，多个因子用逗号分隔
        :param date: 开始日期
        :param end_date: 结束日期
        :return:
            date：日期
            查询因子值
        """
        return self._post(locals(), res_format='csv')

    def run_query(self, table: str, columns: str, conditions: str, count: int):
        """
        run_query api 是模拟了JQDataSDK run_query方法获取财务、宏观、期权等数据
        可查询的数据内容请查看JQData文档
        注：run_query api 只是简单地模拟了python的sqlalchemy.orm.query.Query方法，不能支持复杂的搜索。
            想要更好查询体验，可以使用JQDataSDK。
        :param table: 要查询的数据库和表名，格式为 database + . + tablename 如finance.STK_XR_XD
        :param columns: 所查字段，为空时则查询所有字段，多个字段中间用,分隔。如id,company_id，columns不能有空格等特殊字符
        :param conditions: 查询条件，可以为空，格式为report_date#>=#2006-12-01&report_date#<=#2006-12-31，条件内部#号分隔，
            格式： column # 判断符 # value，多个条件使用&号分隔，表示and，conditions不能有空格等特殊字符
        :param count: 查询条数，count为空时默认1条，最多查询1000条
        :return: 返回的结果顺序为生成时间的顺序
        """
        return self._post(locals(), res_format='csv')

    def get_query_count(self):
        """
        查询剩余条数
        :return: number
        """
        return self._post(dict(), res_format='string')

    def get_fundamentals(self, table: str, columns: str, code: str, date: str, count: int):
        """
        查询股票的市值数据、资产负债数据、现金流数据、利润数据、财务指标数据. 详情通过财务数据列表查看
        :param table: 要查询表名，可选项balance，income，cash_flow，indicator，valuation，
            bank_indicator，security_indicator，insurance_indicator
        :param columns: 所查字段，为空时则查询所有字段，多个字段中间用,分隔。如id,company_id，columns不能有空格等特殊字符
        :param code: 证券代码，多个标的使用,分隔
        :param date: 查询日期2019-03-04或者年度2018或者季度2018q1 2018q2 2018q3 2018q4
        :param count: 查询条数，最多查询1000条。不填count时按date查询
        :return: 返回的结果按日期顺序
        """
        return self._post(locals(), res_format='csv')

    def get_all_factors(self):
        """
        获取聚宽因子库中所有因子的信息
        :return:
            factor 因子代码
            factor_intro 因子名称
            category 因子分类
            category_intro 分类名称
        """
        return self._post(dict(), res_format='csv')

    def get_pause_stocks(self, date: str):
        """
        获取某日停牌股票列表
        :param date: 查询日期，date为空时默认为今天
        :return: 股票代码列表
        """
        return self._post(locals(), res_format='list')

    def get_alpha101(self, code: str, func_name: str, date: str):
        """
        因子来源： 根据 WorldQuant LLC 发表的论文 101 Formulaic Alphas 中给出的 101 个 Alphas 因子公式，
            我们将公式编写成了函数，方便大家使用。
        详细介绍： 函数计算公式、API 调用方法，输入输出值详情请见:数据 - Alpha 101.
        :return:
            股票代码
            因子值
        """
        return self._post(locals(), res_format='csv')

    def get_alpha191(self, code: str, func_name: str, date: str):
        """
        因子来源： 根据国泰君安数量化专题研究报告 - 基于短周期价量特征的多因子选股体系给出了 191 个短周期交易型阿尔法因子。
            为了方便用户快速调用，我们将所有Alpha191因子基于股票的后复权价格做了完整的计算。
            用户只需要指定fq='post’即可获取全新计算的因子数据。
        详细介绍： 函数计算公式、API 调用方法，输入输出值详情请见:数据 - Alpha 191.
        :param code: 标的代码， 多个标的使用,分隔。建议每次请求的标的都是相同类型。支持最多1000个标的查询
        :param func_name: 查询函数名称，如alpha_001，alpha_002等
        :param date: 查询日期
        :return:
            股票代码
            因子值
        """
        return self._post(locals(), res_format='csv')

    def get_fq_factor(self, code: str, fq: str, date: str, end_date: str):
        """
        根据交易时间获取股票和基金复权因子值
        :param code: 单只标的代码
        :param fq: 复权选项 - pre 前复权； post后复权
        :param date: 开始日期
        :param end_date: 结束日期
        :return:
            date: 对应交易日
            标的因子值
        """
        assert fq in ('pre', 'post')
        return self._post(locals(), res_format='csv')

    def get_current_price(self, code: str):
        """
        获取标的的当期价，等同于最新tick中的当前价
        :param code: 标的代码， 多个标的使用,分隔。建议每次请求的标的都是相同类型
        :return:
            code: 标的代码
            current: 当前价格
        """
        return self._post(locals(), res_format='csv')

    def get_call_auction(self, code: str, date: str, end_date: str):
        """
        获取指定时间区间内集合竞价时的tick数据
        :param code: 标的代码，多个标的使用,分隔。支持最多100个标的查询
        :param date: 开始日期
        :param end_date: 结束日期
        :return:
            code: 标的代码
            time 时间 datetime
            current 当前价 float
            volume 累计成交量（股）
            money 累计成交额
            a1_v~a5_v: 五档卖量
            a1_p~a5_p: 五档卖价
            b1_v~b5_v: 五档买量
            b1_p~b5_p: 五档买价
        """
        return self._post(locals(), res_format='csv')
