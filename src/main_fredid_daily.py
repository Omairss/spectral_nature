import releases as relese_data
import source as source_data
import get_series_id as series_data


def main():

    retrun_data=source_data.get_sources()
    print(retrun_data)
    retrun_data=relese_data.get_releases()
    print(retrun_data)
    return_data=series_data.get_series_ids()
    print(retrun_data)

    print("cron ran ")


if __name__=="__main__":
    main()

