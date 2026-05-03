#pragma once

#include "tick_engine.hpp"
#include <deque>
#include <numeric>
#include <unordered_set>

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
                OrderId id = engine->prepare_order(close_short);
                my_orders_.insert(id);
                engine->submit_prepared_order(id);
            }
            // Open long position
            Order buy_order(0, current_price, order_size_, tick.timestamp,
                            Side::BUY, OrderType::LIMIT, 1, sid);
            OrderId id = engine->prepare_order(buy_order);
            my_orders_.insert(id);
            engine->submit_prepared_order(id);
            target_position_ = order_size_;
        }
        // Sell signal: price crosses below MA and we're not short
        else if (current_price < sell_threshold && position_ >= 0) {
            if (position_ > 0) {
                // Close long position first
                Order close_long(0, current_price, position_, tick.timestamp,
                                 Side::SELL, OrderType::LIMIT, 1, sid);
                OrderId id = engine->prepare_order(close_long);
                my_orders_.insert(id);
                engine->submit_prepared_order(id);
            }
            // Open short position
            Order sell_order(0, current_price, order_size_, tick.timestamp,
                             Side::SELL, OrderType::LIMIT, 1, sid);
            OrderId id = engine->prepare_order(sell_order);
            my_orders_.insert(id);
            engine->submit_prepared_order(id);
            target_position_ = -static_cast<int64_t>(order_size_);
        }

        last_tick_ = tick;
    }

    void on_trade(const Trade& trade) override {
        // Check if this trade involves our orders
        bool is_our_buy = my_orders_.count(trade.buy_order_id) > 0;
        bool is_our_sell = my_orders_.count(trade.sell_order_id) > 0;

        if (!is_our_buy && !is_our_sell) return;  // Ignore unrelated trades

        ++trades_executed_;

        if (is_our_buy) {
            position_ += trade.quantity;
        }
        if (is_our_sell) {
            position_ -= trade.quantity;
        }

        // Simple P&L tracking (simplified)
        if (position_ > 0) {
            total_pnl_ += (trade.price - avg_entry_price_) * trade.quantity;
        } else if (position_ < 0) {
            total_pnl_ += (avg_entry_price_ - trade.price) * trade.quantity;
        }
    }

    const char* name() const override { return "MomentumStrategy"; }

    // Getters for analysis
    int64_t position() const { return position_; }
    int64_t pnl() const { return total_pnl_; }
    size_t trades() const { return trades_executed_; }

private:
    size_t window_size_;
    Quantity order_size_;
    std::deque<Price> prices_;
    int64_t position_;
    int64_t target_position_ = 0;
    Price avg_entry_price_ = 0;
    int64_t total_pnl_;
    size_t trades_executed_;
    Tick last_tick_;
    std::unordered_set<OrderId> my_orders_;
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
            OrderId id = engine->prepare_order(bid);
            my_orders_.insert(id);
            engine->submit_prepared_order(id);
        }

        // Place ask (sell side) if we can sell more
        if (can_sell) {
            Order ask(0, mid + spread_/2, quote_size_, tick.timestamp,
                     Side::SELL, OrderType::LIMIT, 2);
            OrderId id = engine->prepare_order(ask);
            my_orders_.insert(id);
            engine->submit_prepared_order(id);
        }
    }

    void on_trade(const Trade& trade) override {
        // Check if this trade involves our orders
        bool is_our_buy = my_orders_.count(trade.buy_order_id) > 0;
        bool is_our_sell = my_orders_.count(trade.sell_order_id) > 0;

        if (!is_our_buy && !is_our_sell) return;  // Ignore unrelated trades

        ++trades_count_;

        if (is_our_buy) {
            position_ += trade.quantity;
        }
        if (is_our_sell) {
            position_ -= trade.quantity;
        }

        // Market makers profit from spread capture
        total_pnl_ += spread_ / 2;  // Simplified P&L
    }

    const char* name() const override { return "MarketMaker"; }

    // Getters for analysis
    int64_t position() const { return position_; }
    size_t trades() const { return trades_count_; }
    int64_t pnl() const { return total_pnl_; }

private:
    Price spread_;
    Quantity quote_size_;
    int64_t max_position_;
    int64_t position_;
    uint64_t tick_count_;
    uint64_t trades_count_;
    int64_t total_pnl_;
    std::unordered_set<OrderId> my_orders_;
};

} // namespace trading
