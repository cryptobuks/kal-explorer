import { Component, OnInit } from '@angular/core';
import { StatusService } from './status.service';
import {Router} from '@angular/router';

@Component({
  selector: 'app-root',
  templateUrl: './app.component.html',
  styleUrls: ['./app.component.scss']
})
export class AppComponent implements OnInit {
  title = 'ui';
  status = {};

  constructor(private router : Router, private statusService : StatusService){ }

  ngOnInit() {
    this.statusService.currentStatus.subscribe((data: [any]) => this.status = data)
    this.statusService.updateStatus();
  }

  goHome() {
    this.router.navigate([''])
  }

  goBlocks() {

  }

  goRichList() {

  }

}