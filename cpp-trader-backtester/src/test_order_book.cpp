#include "order_book.hpp"
#include <iostream>
#include <cassert>

using namespace trading;

void test_partial_fill_volume() {
    std::cout << "Testing partial fill volume tracking...\n";
    
    OrderBook book("TEST");
    
    // Add sell order: 100 shares @ $100
    Order sell_order(1, 1000000, 100, 1000, Side::SELL, OrderType::LIMIT, 1);
    book.add_order(&sell_order);
    
    assert(book.ask_volume() == 100);
    assert(book.best_ask() == 1000000);
    std::cout << "  ✓ Initial ask volume: 100\n";
    
    // Buy 30 shares (partial fill)
    Order buy_order1(2, 1000000, 30, 2000, Side::BUY, OrderType::LIMIT, 2);
    book.add_order(&buy_order1);
    
    assert(book.ask_volume() == 70);  // Should be 70, not 100
    std::cout << "  ✓ After 30 share fill: " << book.ask_volume() << " (expected 70)\n";
    
    // Buy another 40 shares
    Order buy_order2(3, 1000000, 40, 3000, Side::BUY, OrderType::LIMIT, 3);
    book.add_order(&buy_order2);
    
    assert(book.ask_volume() == 30);  // Should be 30
    std::cout << "  ✓ After 40 share fill: " << book.ask_volume() << " (expected 30)\n";
    
    // Buy remaining 30 shares
    Order buy_order3(4, 1000000, 30, 4000, Side::BUY, OrderType::LIMIT, 4);
    book.add_order(&buy_order3);
    
    assert(book.ask_volume() == 0);  // Should be empty
    assert(book.best_ask() == 0);
    std::cout << "  ✓ After final fill: " << book.ask_volume() << " (expected 0)\n";
    
    std::cout << "✅ Partial fill volume tracking: PASSED\n\n";
}

void test_multiple_price_levels() {
    std::cout << "Testing multiple price level volume...\n";
    
    OrderBook book("TEST");
    
    // Add multiple sell orders at different prices
    Order sell1(1, 1000000, 100, 1000, Side::SELL, OrderType::LIMIT, 1);
    Order sell2(2, 1010000, 200, 1000, Side::SELL, OrderType::LIMIT, 1);
    Order sell3(3, 1020000, 300, 1000, Side::SELL, OrderType::LIMIT, 1);
    
    book.add_order(&sell1);
    book.add_order(&sell2);
    book.add_order(&sell3);
    
    assert(book.ask_volume() == 600);
    std::cout << "  ✓ Total ask volume: 600\n";
    
    // Market buy sweeps through levels
    Order market_buy(4, 0, 250, 2000, Side::BUY, OrderType::MARKET, 2);
    book.add_order(&market_buy);
    
    // Should consume all of level 1 (100) and half of level 2 (150)
    assert(book.ask_volume() == 350);  // 600 - 250 = 350
    assert(book.best_ask() == 1010000);  // Level 1 consumed
    std::cout << "  ✓ After market sweep: " << book.ask_volume() << " (expected 350)\n";
    
    std::cout << "✅ Multiple price level volume: PASSED\n\n";
}

void test_fifo_ordering() {
    std::cout << "Testing FIFO price-time priority...\n";
    
    OrderBook book("TEST");
    int trade_count = 0;
    
    book.set_trade_callback([&](const Trade& t) {
        trade_count++;
        std::cout << "  Trade " << trade_count << ": " 
                  << t.quantity << " @ " << (t.price / 10000.0) << "\n";
    });
    
    // Add 3 sell orders at same price (FIFO queue)
    Order sell1(1, 1000000, 100, 1000, Side::SELL, OrderType::LIMIT, 1);
    Order sell2(2, 1000000, 100, 2000, Side::SELL, OrderType::LIMIT, 2);
    Order sell3(3, 1000000, 100, 3000, Side::SELL, OrderType::LIMIT, 3);
    
    book.add_order(&sell1);
    book.add_order(&sell2);
    book.add_order(&sell3);
    
    // Market buy should match in FIFO order
    Order market_buy(4, 0, 250, 4000, Side::BUY, OrderType::MARKET, 4);
    book.add_order(&market_buy);
    
    assert(trade_count == 3);  // Should generate 3 trades
    assert(sell1.status == OrderStatus::FILLED);
    assert(sell2.status == OrderStatus::FILLED);
    assert(sell3.status == OrderStatus::PARTIAL);
    assert(sell3.filled == 50);
    
    std::cout << "  ✓ FIFO order preserved\n";
    std::cout << "✅ FIFO price-time priority: PASSED\n\n";
}

void test_best_bid_regression() {
    std::cout << "Testing best_bid() returns highest bid...\n";

    OrderBook book("TEST");

    // Scenario 1: Higher bid added first, lower bid added second
    // best_bid() should return the higher price
    Order buy_high(1, 1020000, 100, 1000, Side::BUY, OrderType::LIMIT, 1);  // $102.00
    book.add_order(&buy_high);

    assert(book.best_bid() == 1020000);
    std::cout << "  ✓ After adding high bid: best_bid() = 1020000\n";

    Order buy_low(2, 1000000, 100, 2000, Side::BUY, OrderType::LIMIT, 1);   // $100.00
    book.add_order(&buy_low);

    // CRITICAL: best_bid() must return HIGHEST (102), not lowest (100)
    // Historical bug: using rbegin() instead of begin() would return 100
    assert(book.best_bid() == 1020000);
    std::cout << "  ✓ After adding low bid: best_bid() = 1020000 (highest, not lowest)\n";

    std::cout << "✅ Scenario 1 (high first, low second): PASSED\n";

    // Scenario 2: Fresh book - lower bid added first, higher bid added second
    OrderBook book2("TEST2");

    Order buy_low2(3, 990000, 50, 3000, Side::BUY, OrderType::LIMIT, 1);    // $99.00
    book2.add_order(&buy_low2);

    assert(book2.best_bid() == 990000);
    std::cout << "  ✓ After adding low bid: best_bid() = 990000\n";

    Order buy_high2(4, 1010000, 50, 4000, Side::BUY, OrderType::LIMIT, 1);  // $101.00
    book2.add_order(&buy_high2);

    // best_bid() should now return the higher price
    assert(book2.best_bid() == 1010000);
    std::cout << "  ✓ After adding high bid: best_bid() = 1010000 (highest, not lowest)\n";

    std::cout << "✅ Scenario 2 (low first, high second): PASSED\n";
    std::cout << "✅ best_bid() regression test: PASSED\n\n";
}

void test_volume_invariant() {
    std::cout << "Testing volume consistency invariant...\n";

    OrderBook book("TEST");
    Quantity total_executed = 0;

    book.set_trade_callback([&](const Trade& t) {
        total_executed += t.quantity;
    });

    // Add buy orders at price 100: [50, 30, 20] (total 100)
    const Quantity original_total = 100;
    const Price price = 1000000;
    const Quantity expected_executed = 60;

    Order buy1(1, price, 50, 1000, Side::BUY, OrderType::LIMIT, 1);
    Order buy2(2, price, 30, 2000, Side::BUY, OrderType::LIMIT, 1);
    Order buy3(3, price, 20, 3000, Side::BUY, OrderType::LIMIT, 1);

    book.add_order(&buy1);
    book.add_order(&buy2);
    book.add_order(&buy3);

    Quantity initial_bid_volume = book.bid_volume();
    assert(initial_bid_volume == original_total);
    std::cout << "  Initial bid volume: " << initial_bid_volume << "\n";

    // Execute market sell for 60 shares
    Order market_sell(4, 0, 60, 4000, Side::SELL, OrderType::MARKET, 2);
    book.add_order(&market_sell);

    // Verify executed quantity equals expected matched quantity
    std::cout << "  Executed: " << total_executed << " (expected " << expected_executed << ")\n";
    assert(total_executed == expected_executed);
    std::cout << "  ✓ Executed quantity verified\n";

    // Verify volume invariant: executed + remaining = original total
    Quantity remaining = book.bid_volume();
    std::cout << "  Remaining: " << remaining << "\n";
    std::cout << "  Original total: " << original_total << "\n";

    bool invariant_holds = (total_executed + remaining == original_total);

    if (invariant_holds) {
        std::cout << "  INVARIANT CHECK: " << total_executed << " + " << remaining
                  << " = " << original_total << " PASSED\n";
    } else {
        std::cout << "  INVARIANT CHECK FAILED: " << total_executed << " + " << remaining
                  << " != " << original_total << "\n";
    }

    assert(invariant_holds);
    std::cout << "✅ Volume consistency invariant: PASSED\n\n";
}

int main() {
    std::cout << "=== Order Book Correctness Tests ===\n\n";

    try {
        test_partial_fill_volume();
        test_multiple_price_levels();
        test_fifo_ordering();
        test_best_bid_regression();
        test_volume_invariant();

        std::cout << "=== ALL TESTS PASSED ===\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "❌ TEST FAILED: " << e.what() << "\n";
        return 1;
    }
}
