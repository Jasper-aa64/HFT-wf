#pragma once

#include "tick_engine.hpp"
#include <algorithm>
#include <deque>
#include <numeric>
#include <unordered_map>

namespace trading {

// Simple momentum strategy: Buy when price crosses above MA, sell when below
class MomentumStrategy : public Strategy {
public:
    MomentumStrategy(size_t window_size = 20, Quantity order_size = 100)
        : window_size_(window_size), order_size_(order_size),
          position_(0), total_pnl_(0), trades_executed_(0) {}

    void on_tick(const Tick& tick, TickEngine* engine) override {
        // Update price window
        prices_.push_back(tick.price);
        if (prices_.size() > window_size_) {
            prices_.pop_front();
        }

        // Need full window before trading
        if (prices_.size() < window_size_) return;

        // Calculate moving average (in fixed-point)
        Price sum = std::accumulate(prices_.begin(), prices_.end(), Price(0));
        Price ma = sum / static_cast<Price>(prices_.size());
        Price current_price = tick.price;

        // Generate signals with 2% threshold to avoid noise
        Price buy_threshold = ma * 102 / 100;   // MA * 1.02
        Price sell_threshold = ma * 98 / 100;   // MA * 0.98

        // Resolve symbol once per tick — O(1) hash lookup (already registered by engine)
        SymbolId sid = SymbolRegistry::instance().register_symbol(tick.symbol);

        // Buy signal: price crosses above MA and we're not long
        if (current_price > buy_threshold && position_ <= 0) {
            if (position_ < 0) {
                // Close short position first
                Order close_short(0, current_price, -position_, tick.timestamp,
                                  Side::BUY, OrderType::LIMIT, 1, sid);
                submit_owned_order(engine, close_short);
            }
            // Open long position
            Order buy_order(0, current_price, order_size_, tick.timestamp,
                            Side::BUY, OrderType::LIMIT, 1, sid);
            submit_owned_order(engine, buy_order);
            target_position_ = order_size_;
        }
        // Sell signal: price crosses below MA and we're not short
        else if (current_price < sell_threshold && position_ >= 0) {
            if (position_ > 0) {
                // Close long position first
                Order close_long(0, current_price, position_, tick.timestamp,
                                 Side::SELL, OrderType::LIMIT, 1, sid);
                submit_owned_order(engine, close_long);
            }
            // Open short position
            Order sell_order(0, current_price, order_size_, tick.timestamp,
                             Side::SELL, OrderType::LIMIT, 1, sid);
            submit_owned_order(engine, sell_order);
            target_position_ = -static_cast<int64_t>(order_size_);
        }

        last_tick_ = tick;
    }

    void on_trade(const Trade& trade) override {
        // Check if this trade involves our orders
        bool is_our_buy = open_order_qty_.count(trade.buy_order_id) > 0;
        bool is_our_sell = open_order_qty_.count(trade.sell_order_id) > 0;

        if (!is_our_buy && !is_our_sell) return;  // Ignore unrelated trades

        ++trades_executed_;

        if (is_our_buy) {
            apply_buy_fill(trade.price, trade.quantity);
            reduce_open_order(trade.buy_order_id, trade.quantity);
        }
        if (is_our_sell) {
            apply_sell_fill(trade.price, trade.quantity);
            reduce_open_order(trade.sell_order_id, trade.quantity);
        }
    }

    const char* name() const override { return "MomentumStrategy"; }

    // Getters for analysis
    int64_t position() const { return position_; }
    int64_t pnl() const { return total_pnl_; }
    size_t trades() const { return trades_executed_; }
    Price avg_entry_price() const { return avg_entry_price_; }
    size_t open_orders() const { return open_order_qty_.size(); }

private:
    void submit_owned_order(TickEngine* engine, const Order& order) {
        OrderId id = engine->prepare_order(order);
        open_order_qty_[id] = order.quantity;
        engine->submit_prepared_order(id);
    }

    void reduce_open_order(OrderId id, Quantity quantity) {
        auto it = open_order_qty_.find(id);
        if (it == open_order_qty_.end()) return;

        it->second -= quantity;
        if (it->second <= 0) {
            open_order_qty_.erase(it);
        }
    }

    void apply_buy_fill(Price price, Quantity quantity) {
        if (position_ >= 0) {
            Quantity existing = static_cast<Quantity>(position_);
            avg_entry_price_ = weighted_average(avg_entry_price_, existing, price, quantity);
            position_ += quantity;
            return;
        }

        Quantity short_qty = static_cast<Quantity>(-position_);
        Quantity closing_qty = std::min(short_qty, quantity);
        total_pnl_ += (avg_entry_price_ - price) * closing_qty;

        position_ += quantity;
        avg_entry_price_ = position_ > 0 ? price : (position_ == 0 ? 0 : avg_entry_price_);

        if (position_ == 0) {
            avg_entry_price_ = 0;
        }
    }

    void apply_sell_fill(Price price, Quantity quantity) {
        if (position_ <= 0) {
            Quantity existing = static_cast<Quantity>(-position_);
            avg_entry_price_ = weighted_average(avg_entry_price_, existing, price, quantity);
            position_ -= quantity;
            return;
        }

        Quantity long_qty = static_cast<Quantity>(position_);
        Quantity closing_qty = std::min(long_qty, quantity);
        total_pnl_ += (price - avg_entry_price_) * closing_qty;

        position_ -= quantity;
        avg_entry_price_ = position_ < 0 ? price : (position_ == 0 ? 0 : avg_entry_price_);

        if (position_ == 0) {
            avg_entry_price_ = 0;
        }
    }

    Price weighted_average(Price old_price, Quantity old_qty, Price new_price, Quantity new_qty) const {
        Quantity total_qty = old_qty + new_qty;
        if (total_qty == 0) return 0;
        return (old_price * old_qty + new_price * new_qty) / total_qty;
    }

    size_t window_size_;
    Quantity order_size_;
    std::deque<Price> prices_;
    int64_t position_;
    int64_t target_position_ = 0;
    Price avg_entry_price_ = 0;
    int64_t total_pnl_;
    size_t trades_executed_;
    Tick last_tick_;
    std::unordered_map<OrderId, Quantity> open_order_qty_;
};

// Market making strategy: Place orders on both sides
class MarketMakerStrategy : public Strategy {
public:
    MarketMakerStrategy(Price spread = 100, Quantity quote_size = 50,
                       int64_t max_position = 500)
        : spread_(spread), quote_size_(quote_size),
          max_position_(max_position), position_(0),
          tick_count_(0), trades_count_(0), total_pnl_(0) {}

    void on_tick(const Tick& tick, TickEngine* engine) override {
        if (++tick_count_ % 10 != 0) return; // Quote every 10 ticks

        Price mid = tick.price;

        // Risk management: don't quote if position too large
        bool can_buy = position_ < max_position_;
        bool can_sell = position_ > -max_position_;

        // Place bid (buy side) if we can accumulate more
        if (can_buy) {
            Order bid(0, mid - spread_/2, quote_size_, tick.timestamp,
                     Side::BUY, OrderType::LIMIT, 2);
            submit_owned_order(engine, bid);
        }

        // Place ask (sell side) if we can sell more
        if (can_sell) {
            Order ask(0, mid + spread_/2, quote_size_, tick.timestamp,
                     Side::SELL, OrderType::LIMIT, 2);
            submit_owned_order(engine, ask);
        }
    }

    void on_trade(const Trade& trade) override {
        // Check if this trade involves our orders
        bool is_our_buy = open_order_qty_.count(trade.buy_order_id) > 0;
        bool is_our_sell = open_order_qty_.count(trade.sell_order_id) > 0;

        if (!is_our_buy && !is_our_sell) return;  // Ignore unrelated trades

        ++trades_count_;

        if (is_our_buy) {
            apply_buy_fill(trade.price, trade.quantity);
            reduce_open_order(trade.buy_order_id, trade.quantity);
        }
        if (is_our_sell) {
            apply_sell_fill(trade.price, trade.quantity);
            reduce_open_order(trade.sell_order_id, trade.quantity);
        }
    }

    const char* name() const override { return "MarketMaker"; }

    // Getters for analysis
    int64_t position() const { return position_; }
    size_t trades() const { return trades_count_; }
    int64_t pnl() const { return total_pnl_; }
    Price avg_entry_price() const { return avg_entry_price_; }
    size_t open_orders() const { return open_order_qty_.size(); }

private:
    void submit_owned_order(TickEngine* engine, const Order& order) {
        OrderId id = engine->prepare_order(order);
        open_order_qty_[id] = order.quantity;
        engine->submit_prepared_order(id);
    }

    void reduce_open_order(OrderId id, Quantity quantity) {
        auto it = open_order_qty_.find(id);
        if (it == open_order_qty_.end()) return;

        it->second -= quantity;
        if (it->second <= 0) {
            open_order_qty_.erase(it);
        }
    }

    void apply_buy_fill(Price price, Quantity quantity) {
        if (position_ >= 0) {
            Quantity existing = static_cast<Quantity>(position_);
            avg_entry_price_ = weighted_average(avg_entry_price_, existing, price, quantity);
            position_ += quantity;
            return;
        }

        Quantity short_qty = static_cast<Quantity>(-position_);
        Quantity closing_qty = std::min(short_qty, quantity);
        total_pnl_ += (avg_entry_price_ - price) * closing_qty;

        position_ += quantity;
        avg_entry_price_ = position_ > 0 ? price : (position_ == 0 ? 0 : avg_entry_price_);

        if (position_ == 0) {
            avg_entry_price_ = 0;
        }
    }

    void apply_sell_fill(Price price, Quantity quantity) {
        if (position_ <= 0) {
            Quantity existing = static_cast<Quantity>(-position_);
            avg_entry_price_ = weighted_average(avg_entry_price_, existing, price, quantity);
            position_ -= quantity;
            return;
        }

        Quantity long_qty = static_cast<Quantity>(position_);
        Quantity closing_qty = std::min(long_qty, quantity);
        total_pnl_ += (price - avg_entry_price_) * closing_qty;

        position_ -= quantity;
        avg_entry_price_ = position_ < 0 ? price : (position_ == 0 ? 0 : avg_entry_price_);

        if (position_ == 0) {
            avg_entry_price_ = 0;
        }
    }

    Price weighted_average(Price old_price, Quantity old_qty, Price new_price, Quantity new_qty) const {
        Quantity total_qty = old_qty + new_qty;
        if (total_qty == 0) return 0;
        return (old_price * old_qty + new_price * new_qty) / total_qty;
    }

    Price spread_;
    Quantity quote_size_;
    int64_t max_position_;
    int64_t position_;
    uint64_t tick_count_;
    uint64_t trades_count_;
    int64_t total_pnl_;
    Price avg_entry_price_ = 0;
    std::unordered_map<OrderId, Quantity> open_order_qty_;
};

} // namespace trading
