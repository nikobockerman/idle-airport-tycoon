import json
import shutil
import statistics
from decimal import Decimal as D

import npyscreen

UNITS = {
    "M": 10 ** 6,
    "B": 10 ** 9,
    "T": 10 ** 12,
    "q": 10 ** 15,
    "Q": 10 ** 18,
    "s": 10 ** 21,
    "S": 10 ** 24,
    "o": 10 ** 27,
    "N": 10 ** 30,
    "d": 10 ** 33,
    "U": 10 ** 36,
    "Td": 10 ** 39,  # ???
    "Qd": 10 ** 42,  # ???
    "qd": 10 ** 45,  # ???
}


def get_unit(exp):
    return next((k for k, v in UNITS.items() if D(v).log10() == exp))


def print_price(price):
    x = D(price)
    exp = x.adjusted()
    exp -= exp % 3
    return "{} {}".format(x.scaleb(-exp).quantize(D("1.000")), get_unit(exp))


class Database:
    class Price:
        def __init__(self, level, price, unit, discount_level):
            self.level = level
            self.price = price
            self.unit = unit
            self.discount_level = discount_level

        def get_price(self, at_discount_level=None):
            price = self.price * UNITS[self.unit]
            if (
                at_discount_level is None
                or at_discount_level == self.discount_level
            ):
                return price

            return (
                price
                / (1 - self.discount_level / 100)
                * (1 - at_discount_level / 100)
            )

    class Elem:
        def __init__(self, increase_type, increase_percent, last_level, prices):
            self.increase_type = increase_type
            self.increase_percent = increase_percent
            self.last_level = last_level
            self.prices = prices

        def _get_price(self, current_level, current_discount_level):
            discounts = self.prices.get(current_level)
            if discounts is None:
                return None, False

            price = discounts.get(current_discount_level)
            if price is not None:
                return price.get_price(), False

            zero_discount_price = discounts.get(0)
            if zero_discount_price is not None:
                estimated_price = zero_discount_price.get_price(
                    current_discount_level
                )
            else:
                estimated_price = statistics.mean(
                    (
                        price.get_price(current_discount_level)
                        for price in discounts.values()
                    )
                )

            return estimated_price, True

        def get_price_information(self, current_level, current_discount_level):
            current_price, is_estimate = self._get_price(
                current_level, current_discount_level
            )
            if current_price is None:
                return None, None, True

            if self.increase_type == "double":
                payback_price = current_price * 2
            else:
                multiplier = (
                    1 + self.increase_percent / 100 + current_level / 100
                ) / (1 + current_level / 100)
                payback_price = current_price * multiplier / (multiplier - 1)

            return current_price, payback_price, is_estimate

    def __init__(self, path):
        with open(path) as f:
            data = json.load(f)

        self.data = {}
        for research_name, value in data.items():
            prices = {}
            for research_level, level_data in value["prices"].items():
                discounts = {}
                for discount_level, discount_data in level_data.items():
                    discounts[int(discount_level)] = Database.Price(
                        int(research_level),
                        discount_data["price"],
                        discount_data["unit"],
                        int(discount_level),
                    )
                prices[int(research_level)] = discounts

            self.data[research_name] = Database.Elem(
                value["increase_type"],
                value["increase_percent"],
                value["last_level"],
                prices,
            )


class PaybackValue:
    def __init__(
        self, research, cost, is_estimate, payback_value, old_level, new_level
    ):
        self.research = research
        self.cost = cost
        self.is_estimate = is_estimate
        self.payback_value = payback_value
        self.old_level = old_level
        self.new_level = new_level


class Research:
    def __init__(self, name, start, db_elem):
        self.name = name
        self.level = start
        self.db_elem = db_elem
        if self.level is None:
            self.level = 0
            if self.db_elem.increase_type == "double":
                self.level = 1

    def increase_level(self):
        if self.db_elem.increase_type == "double":
            self.level *= 2
        else:
            self.level += self.db_elem.increase_percent

    def get_payback_values(self, current_discount_level, start_level=None):
        current_level = start_level
        if current_level is None:
            current_level = self.level
        while True:
            if (
                self.db_elem.last_level is not None
                and current_level > self.db_elem.last_level
            ):
                break
            if (
                self.db_elem.last_level is None
                and current_level > list(self.db_elem.prices.keys())[-1]
            ):
                break

            if self.db_elem.increase_type == "double":
                next_level = current_level * 2
            else:
                next_level = current_level + self.db_elem.increase_percent

            (
                cost,
                payback_value,
                is_estimate,
            ) = self.db_elem.get_price_information(
                current_level, current_discount_level
            )
            yield PaybackValue(
                self,
                cost,
                is_estimate,
                payback_value,
                current_level,
                next_level,
            )

            current_level = next_level


class State:
    def __init__(self, path, database):
        self._path = path
        with open(self._path) as f:
            data = json.load(f)

        self.discount_level = data["discount_level"]
        self.researches = []
        for key, value in data["researches"].items():
            self.researches.append(Research(key, value, database.data[key],))

        for key, elem in database.data.items():
            if next((True for r in self.researches if r.name == key), False):
                continue

            self.researches.append(Research(key, False, None, elem))

    def save(self):
        new_data = {
            "discount_level": self.discount_level,
            "researches": {
                research.name: research.level for research in self.researches
            },
        }

        tmp_path = self._path + ".tmp"
        with open(tmp_path, "w") as f:
            f.write(json.dumps(new_data, indent=4))
        shutil.move(tmp_path, self._path)


def get_next_payback_values(researches, current_discount_level):
    values = []
    for r in researches:
        value = next(r.get_payback_values(current_discount_level), None)
        if value is not None:
            values.append(value)

    def sort(l):
        return sorted(
            l,
            key=lambda x: x.payback_value if x.payback_value is not None else 0,
        )

    values = sort(values)
    while values:
        v = values.pop(0)
        next_value = next(
            v.research.get_payback_values(current_discount_level, v.new_level),
            None,
        )
        yield v
        if next_value is not None:
            values.append(next_value)
            values = sort(values)


class NextResearches(npyscreen.Form):
    def __init__(self, state, *args, **kwargs):
        self._state = state
        self._value_generator = get_next_payback_values(
            self._state.researches, self._state.discount_level
        )
        self._grid = None
        super().__init__(args, kwargs)

    def create(self):
        self._grid = self.add(
            npyscreen.SimpleGrid,
            name="Grid",
            columns=5,
            select_whole_line=True,
            max_height=10,
        )
        self._grid.values = []
        for _ in range(10):
            self._grid.values.append(
                self._get_row_data(next(self._value_generator))
            )

        def mark_done():
            research = self._grid.values[0][5]
            research.increase_level()
            self._state.save()
            del self._grid.values[0]
            self._grid.values.append(
                self._get_row_data(next(self._value_generator))
            )
            self._grid.update()

        self._mark_done_button = self.add(
            npyscreen.ButtonPress,
            name="MarkDone",
            when_pressed_function=mark_done,
        )

    @staticmethod
    def _get_row_data(value):
        def print_cost():
            result = print_price(value.cost)
            if value.is_estimate:
                return "* " + result
            return result

        return [
            value.research.name,
            value.old_level,
            value.new_level,
            print_cost(),
            print_price(value.payback_value)
            if value.payback_value is not None
            else "???",
            value.research,
        ]

    def afterEditing(self):
        self.parentApp.setNextForm(None)


class IdleAirport(npyscreen.NPSAppManaged):
    def onStart(self):
        self.database = Database("database.json")
        self.state = State("state.json", self.database)
        self.addForm("MAIN", NextResearches, self.state)


def main():
    app = IdleAirport()
    app.run()
    return


if __name__ == "__main__":
    main()
