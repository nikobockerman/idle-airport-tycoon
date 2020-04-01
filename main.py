import json
import shutil
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

        def get_price(self):
            return self.price * UNITS[self.unit]

    class Elem:
        def __init__(self, increase_type, increase_percent, last_level, prices):
            self.increase_type = increase_type
            self.increase_percent = increase_percent
            self.last_level = last_level
            self.prices = prices

        def get_next_payback_value(self, current_level):
            if current_level not in self.prices:
                return None
            if self.increase_type == "double":
                return self.prices[current_level].get_price() * 2

            multiplier = (
                1 + self.increase_percent / 100 + current_level / 100
            ) / (1 + current_level / 100)
            return (
                self.prices[current_level].get_price()
                * multiplier
                / (multiplier - 1)
            )

    def __init__(self, path):
        with open(path) as f:
            data = json.load(f)

        self.data = {}
        for key, value in data.items():
            prices = {
                int(k): Database.Price(
                    int(k), v["price"], v["unit"], v["discount_level"]
                )
                for k, v in value["prices"].items()
            }
            self.data[key] = Database.Elem(
                value["increase_type"],
                value["increase_percent"],
                value["last_level"],
                prices,
            )


class PaybackValue:
    def __init__(self, research, cost, old_level, new_level, payback_value):
        self.research = research
        self.cost = cost
        self.old_level = old_level
        self.new_level = new_level
        self.payback_value = payback_value


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

    def get_payback_values(self, start_level=None):
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

            yield PaybackValue(
                self,
                self.db_elem.prices[current_level].get_price(),
                current_level,
                next_level,
                self.db_elem.get_next_payback_value(current_level),
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


def get_next_payback_values(researches):
    values = []
    for r in researches:
        value = next(r.get_payback_values(), None)
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
        next_value = next(v.research.get_payback_values(v.new_level), None)
        yield v
        if next_value is not None:
            values.append(next_value)
            values = sort(values)


class NextResearches(npyscreen.Form):
    def __init__(self, state, *args, **kwargs):
        self._state = state
        self._value_generator = get_next_payback_values(self._state.researches)
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
        return [
            value.research.name,
            value.old_level,
            value.new_level,
            print_price(value.cost),
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
    state = None
    for index, value in enumerate(get_next_payback_values(state.researches)):
        print(
            "{:20}: {:4} => {:4}: {:>9} --- {:>9}".format(
                value.research.name,
                value.old_level,
                value.new_level,
                print_price(value.cost),
                print_price(value.payback_value)
                if value.payback_value is not None
                else "???",
            )
        )
        if index >= 9:
            break
    # for research in state.researches:
    #    print(research.name)
    #    for payback_value in research.get_payback_values():
    #        print(
    #            "{} => {}: {} --- {}".format(
    #                payback_value.old_level,
    #                payback_value.new_level,
    #                print_price(payback_value.cost),
    #                print_price(payback_value.payback_value),
    #            )
    #        )
    # menu = ConsoleMenu("IdleAirport", "Something")
    # item = MenuItem("Item")
    # menu.append_item(item)
    # menu.show()


if __name__ == "__main__":
    main()
